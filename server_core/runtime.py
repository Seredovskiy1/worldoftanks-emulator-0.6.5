import heapq
import selectors
import socket
import time


FORCED_POSITION_BROADCAST_INTERVAL = 1.0
LONG_CALLBACK_THRESHOLD_MS = 100.0
SOCKET_BUFFER_BYTES = 1 << 20
MAX_SOCKET_READS_PER_TURN = 32
OUTBOUND_QUEUE_WARN_THRESHOLD = 128
RUNTIME_DIAG_INTERVAL = 5.0


class ScheduledCall:
    __slots__ = ("when", "seq", "callback", "cancelled")

    def __init__(self, when, seq, callback):
        self.when = when
        self.seq = seq
        self.callback = callback
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class PacketSocketAdapter:
    __slots__ = ("runtime", "sock", "data", "addr", "used")

    def __init__(self, runtime, sock, data, addr):
        self.runtime = runtime
        self.sock = sock
        self.data = data
        self.addr = addr
        self.used = False

    def recvfrom(self, _size):
        if self.used:
            raise BlockingIOError()
        self.used = True
        return self.data, self.addr

    def sendto(self, data, addr):
        return self.runtime.sendto(self.sock, data, addr)


class EventLoopRuntime:
    def __init__(self, module):
        self.module = module
        self.selector = selectors.DefaultSelector()
        self.scheduled = []
        self.outbound = []
        self.seq = 0
        self.running = False
        self.login_sock = None
        self.base_sock = None
        self.session_ticks = set()
        self.battle_tick_running = False
        self.battle_tick_next = 0.0
        self.battle_tick_count = 0
        self.battle_tick_last_dbg = time.time()
        self.battle_tick_last_count = 0
        self.battle_tick_max_ms = 0.0
        self.outbound_max_len = 0
        self.read_burst_max = 0
        self.runtime_diag_last = time.time()

    def call_later(self, delay, callback):
        self.seq += 1
        item = ScheduledCall(time.time() + max(0.0, float(delay)), self.seq, callback)
        heapq.heappush(self.scheduled, (item.when, item.seq, item))
        return item

    def sendto(self, sock, data, addr):
        if isinstance(sock, PacketSocketAdapter):
            sock = sock.sock
        self.outbound.append((sock, bytes(data), addr))
        self.outbound_max_len = max(self.outbound_max_len, len(self.outbound))
        return len(data)

    def _next_session_tick_byte(self, sess):
        tick_byte = int(sess.get("session_tick_counter", 0)) & 0xFF
        sess["session_tick_counter"] = (tick_byte + 1) & 0xFF
        return tick_byte

    def start_session_tick(self, sock, addr, sess):
        key = id(sess)
        if key in self.session_ticks:
            return
        self.session_ticks.add(key)
        raw_sock = sock.sock if isinstance(sock, PacketSocketAdapter) else sock

        def tick():
            if not sess.get("init_sent") or sess.get("addr") != addr:
                self.session_ticks.discard(key)
                return
            tick_byte = self._next_session_tick_byte(sess)
            pkt = self.module.msg_fixed(0x0D, self.module.struct.pack("<B", tick_byte))
            pkt = self.module.build_channel_packet(pkt, sess, reliable=False)
            pkt = self.module.bw_encrypt_packet(pkt, sess["bf_key"])
            self.sendto(raw_sock, pkt, addr)
            self.call_later(self._tick_interval(), tick)

        self.call_later(self._tick_interval(), tick)

    def _tick_interval(self):
        return float(getattr(self.module, "BATTLE_MOTION_TICK", 0.05))

    def start_battle_tick(self, sock):
        if self.battle_tick_running:
            return
        self.battle_tick_running = True
        self.battle_tick_next = time.time()
        raw_sock = sock.sock if isinstance(sock, PacketSocketAdapter) else sock
        self.call_later(0.0, lambda: self._battle_tick(raw_sock))
        print(f"[*] Battle tick loop started ({1.0 / self.module.BATTLE_MOTION_TICK:.0f} Hz)")

    def _battle_tick(self, sock):
        if not self.battle_tick_running:
            return
        target_interval = self._tick_interval()
        self.battle_tick_next += target_interval
        if self.battle_tick_next < time.time() - target_interval:
            self.battle_tick_next = time.time()
        self._run_battle_tick_once(sock)
        self.call_later(max(0.0, self.battle_tick_next - time.time()),
                        lambda: self._battle_tick(sock))

    def _run_battle_tick_once(self, sock):
        mod = self.module
        iter_start = time.time()
        with mod.battle_lock:
            active_sessions = []
            prebattle_sessions = []
            for sess in mod.active_battle_accounts.values():
                if not sess.get("battle_bundle_sent"):
                    continue
                if sess.get("battle_period_active"):
                    active_sessions.append(sess)
                else:
                    prebattle_sessions.append(sess)
        sessions = active_sessions + prebattle_sessions
        if not sessions:
            return
        self.battle_tick_count += 1
        remote_payloads = []
        for sess in sessions:
            in_prebattle = not sess.get("battle_period_active")
            flags = sess.get("battle_motion_flags", 0)
            if in_prebattle:
                pos = sess.get("battle_pos", mod.ARENA_SPAWN_POS[mod.ARENA_TYPE_KARELIA])
                yaw = float(sess.get("battle_yaw", 0.0))
                speed = 0.0
                rspeed = 0.0
                addr = sess.get("addr")
                if addr:
                    mod.send_avatar_messages(sock, addr, sess,
                                             mod.build_battle_motion_tick(pos, yaw, speed, rspeed),
                                             "",
                                             reliable=False)
            elif sess.get("server_vehicle_authoritative", True):
                pos, yaw, speed, rspeed = mod.advance_battle_motion(sess, flags)
                addr = sess.get("addr")
                if addr:
                    mod.send_avatar_messages(sock, addr, sess,
                                             mod.build_battle_motion_tick(pos, yaw, speed, rspeed),
                                             "",
                                             reliable=False)
            else:
                pos = mod.get_effective_vehicle_pos(
                    sess, sess.get("battle_pos", mod.ARENA_SPAWN_POS[mod.ARENA_TYPE_KARELIA]))
                yaw = float(sess.get("client_vehicle_yaw", sess.get("battle_yaw", 0.0)))
            source_account_id = sess.get("account_id")
            remote_id = mod.get_remote_vehicle_id(sess)
            _yaw, gun_pitch, turret_yaw = mod.get_remote_vehicle_angles(sess)
            remote_pos = mod.get_effective_vehicle_pos(sess, pos)
            if (not in_prebattle and
                    not sess.get("server_vehicle_authoritative", True) and
                    mod.is_recent_client_vehicle_position(sess)):
                yaw = float(sess.get("client_vehicle_yaw", yaw))
            remote_msg = mod.build_vehicle_motion_update_for(
                remote_id, remote_pos, yaw, gun_pitch, turret_yaw)
            for observer in sessions:
                if observer is sess or not observer.get("addr"):
                    continue
                if observer.get("battle_match_id") != sess.get("battle_match_id"):
                    continue
                if source_account_id not in observer.setdefault("known_remote_accounts", set()):
                    mod.send_remote_vehicle(sock, observer, sess)
                payload = None
                for entry in remote_payloads:
                    if entry[0] is observer:
                        payload = entry[1]
                        break
                if payload is None:
                    payload = []
                    remote_payloads.append((observer, payload))
                payload.append(remote_msg)
        for observer, payload in remote_payloads:
            mod.send_avatar_messages(sock, observer.get("addr"), observer,
                                     b"".join(payload), "", reliable=False)
        self._broadcast_filter_resets(sock, active_sessions, prebattle_sessions,
                                      iter_start)
        processed_matches = set()
        for sess in active_sessions:
            match_id = sess.get("battle_match_id")
            if match_id in processed_matches:
                continue
            processed_matches.add(match_id)
            mod.process_base_capture(sock, match_id)
        iter_ms = (time.time() - iter_start) * 1000.0
        self.battle_tick_max_ms = max(self.battle_tick_max_ms, iter_ms)
        now = time.time()
        if now - self.battle_tick_last_dbg >= 5.0:
            elapsed = now - self.battle_tick_last_dbg
            ticks_done = self.battle_tick_count - self.battle_tick_last_count
            actual_hz = ticks_done / max(0.001, elapsed)
            print(f"    [tick] sessions={len(active_sessions)}+pb{len(prebattle_sessions)} "
                  f"actualHz={actual_hz:.1f} "
                  f"(target={1.0 / self._tick_interval():.0f}) "
                  f"maxIterMs={self.battle_tick_max_ms:.1f}")
            self.battle_tick_last_dbg = now
            self.battle_tick_last_count = self.battle_tick_count
            self.battle_tick_max_ms = 0.0

    def _broadcast_filter_resets(self, sock, active_sessions, prebattle_sessions,
                                 now):
        if not active_sessions:
            return
        mod = self.module
        interval = float(getattr(mod, "FORCED_POSITION_BROADCAST_INTERVAL",
                                 FORCED_POSITION_BROADCAST_INTERVAL))
        if interval <= 0.0:
            return
        space_id = int(getattr(mod, "SPACE_ID", 1))
        own_id = int(getattr(mod, "PLAYER_VEHICLE_ID", 200))
        spawn_default = mod.ARENA_SPAWN_POS[mod.ARENA_TYPE_KARELIA]
        for sess in active_sessions:
            addr = sess.get("addr")
            if not addr:
                continue
            last_reset = sess.get("battle_last_filter_reset_time")
            if last_reset is None:
                sess["battle_last_filter_reset_time"] = now
                continue
            if now - float(last_reset) < interval:
                continue
            pos = sess.get("battle_pos") or spawn_default
            yaw = float(sess.get("battle_yaw", 0.0))
            msgs = mod.build_forced_position(own_id, pos, yaw,
                                             space_id=space_id, vehicle_id=0)
            mod.send_avatar_messages(sock, addr, sess, msgs, "",
                                     reliable=False)
            sess["battle_last_filter_reset_time"] = now

    def _timeout(self):
        if self.outbound:
            return 0
        if not self.scheduled:
            return 1.0
        return max(0.0, self.scheduled[0][0] - time.time())

    def _run_due(self):
        now = time.time()
        while self.scheduled and self.scheduled[0][0] <= now:
            _when, _seq, item = heapq.heappop(self.scheduled)
            if item.cancelled:
                continue
            cb_start = time.time()
            item.callback()
            cb_ms = (time.time() - cb_start) * 1000.0
            if cb_ms > LONG_CALLBACK_THRESHOLD_MS:
                print(f"[!] scheduled callback blocked the loop "
                      f"for {cb_ms:.0f}ms")
            now = time.time()

    def _flush_outbound(self):
        while self.outbound:
            sock, data, addr = self.outbound.pop(0)
            try:
                sock.sendto(data, addr)
            except OSError:
                pass

    def _report_runtime_diag(self):
        now = time.time()
        if now - self.runtime_diag_last < RUNTIME_DIAG_INTERVAL:
            return
        if (self.outbound_max_len > OUTBOUND_QUEUE_WARN_THRESHOLD or
                self.read_burst_max >= MAX_SOCKET_READS_PER_TURN):
            print(f"    [net] maxOutbound={self.outbound_max_len} "
                  f"maxReadBurst={self.read_burst_max}")
        self.outbound_max_len = 0
        self.read_burst_max = 0
        self.runtime_diag_last = now

    def _read_socket(self, sock, handler):
        processed = 0
        while processed < MAX_SOCKET_READS_PER_TURN:
            try:
                data, addr = sock.recvfrom(65535)
            except BlockingIOError:
                self.read_burst_max = max(self.read_burst_max, processed)
                return
            except ConnectionResetError:
                self.read_burst_max = max(self.read_burst_max, processed)
                return
            processed += 1
            adapter = PacketSocketAdapter(self, sock, data, addr)
            try:
                h_start = time.time()
                handler(adapter)
                h_ms = (time.time() - h_start) * 1000.0
                if h_ms > LONG_CALLBACK_THRESHOLD_MS:
                    print(f"[!] packet handler blocked the loop "
                          f"for {h_ms:.0f}ms")
            except ConnectionResetError:
                pass
            if self.outbound:
                self._flush_outbound()
        self.read_burst_max = max(self.read_burst_max, processed)

    def _tune_socket(self, sock):
        for opt in (socket.SO_RCVBUF, socket.SO_SNDBUF):
            try:
                sock.setsockopt(socket.SOL_SOCKET, opt, SOCKET_BUFFER_BYTES)
            except OSError:
                pass

    def run(self):
        mod = self.module
        mod.SERVER_RUNTIME = self
        mod.init_database()
        self.login_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.login_sock.setblocking(False)
        self._tune_socket(self.login_sock)
        self.login_sock.bind(("0.0.0.0", mod.LOGIN_PORT))
        self.base_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.base_sock.setblocking(False)
        self._tune_socket(self.base_sock)
        self.base_sock.bind(("0.0.0.0", mod.BASEAPP_PORT))
        self.selector.register(self.login_sock, selectors.EVENT_READ, mod.handle_loginapp)
        self.selector.register(self.base_sock, selectors.EVENT_READ, mod.handle_baseapp)
        print(f"[*] WoT 0.6.5 Emulator | LoginApp:{mod.LOGIN_PORT} | BaseApp:{mod.BASEAPP_PORT}")
        print(f"[*] PUBLIC_HOST={mod.PUBLIC_HOST} (advertised to clients in LoginReply)")
        if mod.PUBLIC_HOST in ("127.0.0.1", "localhost"):
            print("[!] PUBLIC_HOST=127.0.0.1 - only local clients can connect.")
            print("[!] For remote players, set env: WOT_PUBLIC_HOST=<your_public_ip_or_domain>")
        prewarm = getattr(mod, "prewarm_static_obstacles_for_enabled_maps", None)
        if prewarm is not None:
            prewarm()
        print("[*] Запускай гру і тисни Connect!\n")
        self.running = True
        while self.running:
            self._run_due()
            self._flush_outbound()
            self._report_runtime_diag()
            for key, _mask in self.selector.select(self._timeout()):
                self._read_socket(key.fileobj, key.data)
            self._run_due()
            self._flush_outbound()
            self._report_runtime_diag()
