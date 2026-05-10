import struct
import threading
import unittest

import emulator
from server_core import EventLoopRuntime
from server_core.runtime import MAX_SOCKET_READS_PER_TURN


class _FakeBattleModule:
    BATTLE_MOTION_TICK = 0.05
    ARENA_TYPE_KARELIA = emulator.ARENA_TYPE_KARELIA
    ARENA_SPAWN_POS = emulator.ARENA_SPAWN_POS
    PLAYER_VEHICLE_ID = emulator.PLAYER_VEHICLE_ID
    SPACE_ID = emulator.SPACE_ID
    FORCED_POSITION_BROADCAST_INTERVAL = 1.0

    def __init__(self):
        self.battle_lock = threading.RLock()
        self.active_battle_accounts = {}
        self.advance_calls = []
        self.base_capture_calls = []
        self.avatar_sends = []
        self.remote_vehicle_intros = []

    def build_battle_motion_tick(self, pos, yaw, speed, rspeed):
        return emulator.build_battle_motion_tick(pos, yaw, speed, rspeed)

    def build_vehicle_motion_update_for(self, vid, pos, yaw, pitch, roll):
        return emulator.build_vehicle_motion_update_for(vid, pos, yaw, pitch, roll)

    def build_forced_position(self, entity_id, pos, yaw, space_id=1, vehicle_id=0):
        return emulator.build_forced_position(entity_id, pos, yaw,
                                              space_id=space_id,
                                              vehicle_id=vehicle_id)

    def advance_battle_motion(self, sess, flags):
        self.advance_calls.append((sess, flags))
        pos = sess.get("battle_pos", self.ARENA_SPAWN_POS[self.ARENA_TYPE_KARELIA])
        yaw = float(sess.get("battle_yaw", 0.0))
        return pos, yaw, 0.0, 0.0

    def send_avatar_messages(self, sock, addr, sess, msgs, label, reliable=False):
        self.avatar_sends.append({
            "addr": addr,
            "sess": sess,
            "msgs": msgs,
            "label": label,
            "reliable": reliable,
        })
        return True

    def process_base_capture(self, sock, match_id):
        self.base_capture_calls.append(match_id)

    def send_remote_vehicle(self, sock, observer, sess):
        self.remote_vehicle_intros.append((observer, sess))

    def get_effective_vehicle_pos(self, sess, fallback=None):
        pos = sess.get("battle_pos")
        if pos is not None:
            return pos
        if fallback is not None:
            return fallback
        return self.ARENA_SPAWN_POS[self.ARENA_TYPE_KARELIA]

    def get_remote_vehicle_id(self, sess):
        return 1000 + int(sess.get("account_id") or 0)

    def get_remote_vehicle_angles(self, sess):
        yaw = float(sess.get("battle_yaw", 0.0))
        return yaw, 0.0, yaw

    def is_recent_client_vehicle_position(self, sess):
        return False


def _make_session(account_id, period_active):
    spawn = emulator.ARENA_SPAWN_POS[emulator.ARENA_TYPE_KARELIA]
    return {
        "account_id": account_id,
        "addr": ("127.0.0.1", 12000 + account_id),
        "battle_bundle_sent": True,
        "battle_period_active": period_active,
        "battle_pos": spawn,
        "battle_yaw": 0.0,
        "battle_motion_flags": 0,
        "battle_match_id": 1,
        "server_vehicle_authoritative": True,
        "known_remote_accounts": set(),
    }


class RuntimeCoreTests(unittest.TestCase):
    def test_a20_speed_limits_are_xml_values(self):
        vehicle = next(v for v in emulator.load_all_vehicles() if v["name"] == "A-20")
        forward, backward = emulator.get_vehicle_speed_limits(vehicle)
        self.assertAlmostEqual(forward, 20.0)
        self.assertAlmostEqual(backward, 5.555555555555555)
        self.assertAlmostEqual(forward * 3.6, 72.0)

    def test_battle_motion_target_uses_vehicle_speed(self):
        vehicle = next(v for v in emulator.load_all_vehicles() if v["name"] == "A-20")
        speed, rspeed = emulator.battle_motion_targets(1, vehicle)
        self.assertAlmostEqual(speed, 20.0)
        self.assertGreaterEqual(rspeed, 0.0)

    def test_channel_reliable_and_unreliable_sequences_are_separate(self):
        sess = {"in_seq_at": 0}
        emulator.build_channel_packet(b"", sess, reliable=True)
        emulator.build_channel_packet(b"", sess, reliable=False)
        emulator.build_channel_packet(b"", sess, reliable=True)
        self.assertEqual(sess["out_channel_seq"], 2)
        self.assertEqual(sess["out_nub_seq"], 1)

    def test_in_seq_initializes_from_first_observed_high_sequence(self):
        sess = {}

        emulator.update_in_seq_state(sess, 784)

        self.assertTrue(sess["in_seq_initialized"])
        self.assertEqual(sess["in_seq_at"], 785)
        self.assertEqual(sess["in_seq_buffered"], set())

    def test_in_seq_advances_to_latest_observed_sequence(self):
        sess = {}

        emulator.update_in_seq_state(sess, 784)
        emulator.update_in_seq_state(sess, 1043)

        self.assertEqual(sess["in_seq_at"], 1044)

    def test_in_seq_does_not_move_backwards(self):
        sess = {}

        emulator.update_in_seq_state(sess, 1043)
        emulator.update_in_seq_state(sess, 784)

        self.assertEqual(sess["in_seq_at"], 1044)

    def test_scheduler_order_and_cancel(self):
        runtime = EventLoopRuntime(emulator)
        seen = []
        runtime.call_later(0.0, lambda: seen.append("b"))
        cancelled = runtime.call_later(0.0, lambda: seen.append("x"))
        runtime.call_later(0.0, lambda: seen.append("c"))
        cancelled.cancel()
        runtime._run_due()
        self.assertEqual(seen, ["b", "c"])

    def test_session_tick_counters_are_independent(self):
        runtime = EventLoopRuntime(emulator)
        sess_a = {}
        sess_b = {}

        self.assertEqual(runtime._next_session_tick_byte(sess_a), 0)
        self.assertEqual(runtime._next_session_tick_byte(sess_a), 1)
        self.assertEqual(runtime._next_session_tick_byte(sess_b), 0)
        self.assertEqual(runtime._next_session_tick_byte(sess_b), 1)

    def test_socket_read_loop_limits_burst_and_flushes_outbound(self):
        class BurstSocket:
            def __init__(self, count):
                self.remaining = count
                self.sent = []

            def recvfrom(self, _size):
                if self.remaining <= 0:
                    raise BlockingIOError()
                self.remaining -= 1
                return b"x", ("127.0.0.1", 20000 + self.remaining)

            def sendto(self, data, addr):
                self.sent.append((bytes(data), addr))
                return len(data)

        runtime = EventLoopRuntime(emulator)
        sock = BurstSocket(MAX_SOCKET_READS_PER_TURN + 5)
        seen = []

        def handler(adapter):
            data, addr = adapter.recvfrom(65535)
            seen.append((data, addr))
            adapter.sendto(b"y", addr)

        runtime._read_socket(sock, handler)

        self.assertEqual(len(seen), MAX_SOCKET_READS_PER_TURN)
        self.assertEqual(len(sock.sent), MAX_SOCKET_READS_PER_TURN)
        self.assertEqual(sock.remaining, 5)
        self.assertEqual(runtime.outbound, [])

    def test_battle_motion_tick_uses_detailed_position(self):
        pos = (-360.0, 100.0, -360.0)
        yaw = 0.5
        pkt = emulator.build_battle_motion_tick(pos, yaw, 5.0, 0.0)
        self.assertEqual(pkt[0], emulator.CLIENT_DETAILED_POSITION_MSG_ID)
        self.assertNotEqual(pkt[0], emulator.CLIENT_AVATAR_UPDATE_NOALIAS_FULLPOS_YPR_MSG_ID)
        eid, x, y, z, roll, pitch, yaw_out = struct.unpack("<I3f3f", pkt[1:])
        self.assertEqual(eid, emulator.PLAYER_VEHICLE_ID)
        self.assertAlmostEqual(x, pos[0])
        self.assertAlmostEqual(y, pos[1])
        self.assertAlmostEqual(z, pos[2])
        self.assertAlmostEqual(yaw_out, yaw)

    def test_prebattle_session_emits_static_detailed_position(self):
        fake = _FakeBattleModule()
        sess = _make_session(account_id=1, period_active=False)
        fake.active_battle_accounts[1] = sess
        runtime = EventLoopRuntime(fake)

        runtime._run_battle_tick_once(sock=object())

        self.assertEqual(fake.advance_calls, [])
        self.assertEqual(fake.base_capture_calls, [])
        self.assertEqual(len(fake.avatar_sends), 1)
        send = fake.avatar_sends[0]
        self.assertFalse(send["reliable"])
        msgs = send["msgs"]
        self.assertEqual(msgs[0], emulator.CLIENT_DETAILED_POSITION_MSG_ID)
        eid, x, y, z, _roll, _pitch, _yaw = struct.unpack("<I3f3f", msgs[1:])
        self.assertEqual(eid, emulator.PLAYER_VEHICLE_ID)
        spawn = emulator.ARENA_SPAWN_POS[emulator.ARENA_TYPE_KARELIA]
        self.assertAlmostEqual(x, spawn[0], places=3)
        self.assertAlmostEqual(y, spawn[1], places=3)
        self.assertAlmostEqual(z, spawn[2], places=3)

    def test_active_session_still_advances_motion_and_runs_base_capture(self):
        fake = _FakeBattleModule()
        sess = _make_session(account_id=2, period_active=True)
        fake.active_battle_accounts[2] = sess
        runtime = EventLoopRuntime(fake)

        runtime._run_battle_tick_once(sock=object())

        self.assertEqual(len(fake.advance_calls), 1)
        called_sess, called_flags = fake.advance_calls[0]
        self.assertIs(called_sess, sess)
        self.assertEqual(called_flags, 0)
        self.assertEqual(fake.base_capture_calls, [1])
        self.assertEqual(len(fake.avatar_sends), 1)
        self.assertEqual(fake.avatar_sends[0]["msgs"][0],
                         emulator.CLIENT_DETAILED_POSITION_MSG_ID)
        self.assertIsNotNone(sess.get("battle_last_filter_reset_time"))

    def test_first_active_tick_records_filter_reset_baseline_without_emitting(self):
        fake = _FakeBattleModule()
        sess = _make_session(account_id=10, period_active=True)
        fake.active_battle_accounts[10] = sess
        runtime = EventLoopRuntime(fake)

        runtime._run_battle_tick_once(sock=object())

        forced_sends = [s for s in fake.avatar_sends
                        if s["msgs"][:1] == bytes([emulator.CLIENT_FORCED_POSITION_MSG_ID])]
        self.assertEqual(forced_sends, [])
        self.assertIsNotNone(sess.get("battle_last_filter_reset_time"))

    def test_active_tick_emits_forced_position_after_reset_interval(self):
        fake = _FakeBattleModule()
        sess = _make_session(account_id=11, period_active=True)
        fake.active_battle_accounts[11] = sess
        runtime = EventLoopRuntime(fake)

        runtime._run_battle_tick_once(sock=object())
        baseline = sess.get("battle_last_filter_reset_time")
        self.assertIsNotNone(baseline)
        stale = baseline - 5.0
        sess["battle_last_filter_reset_time"] = stale

        before = len(fake.avatar_sends)
        runtime._run_battle_tick_once(sock=object())
        new_sends = fake.avatar_sends[before:]

        forced_sends = [s for s in new_sends
                        if s["msgs"][:1] == bytes([emulator.CLIENT_FORCED_POSITION_MSG_ID])]
        self.assertEqual(len(forced_sends), 1)
        forced = forced_sends[0]
        self.assertFalse(forced["reliable"])
        self.assertEqual(forced["msgs"][0], emulator.CLIENT_FORCED_POSITION_MSG_ID)
        eid = struct.unpack("<I", forced["msgs"][1:5])[0]
        self.assertEqual(eid, emulator.PLAYER_VEHICLE_ID)
        self.assertGreater(sess["battle_last_filter_reset_time"], stale)

    def test_forced_position_bundle_excludes_known_remote_vehicles(self):
        fake = _FakeBattleModule()
        sess_a = _make_session(account_id=21, period_active=True)
        sess_b = _make_session(account_id=22, period_active=True)
        sess_a["known_remote_accounts"].add(22)
        sess_b["known_remote_accounts"].add(21)
        fake.active_battle_accounts[21] = sess_a
        fake.active_battle_accounts[22] = sess_b
        runtime = EventLoopRuntime(fake)

        runtime._run_battle_tick_once(sock=object())
        sess_a["battle_last_filter_reset_time"] = (
            sess_a["battle_last_filter_reset_time"] - 5.0)
        sess_b["battle_last_filter_reset_time"] = (
            sess_b["battle_last_filter_reset_time"] - 5.0)

        before = len(fake.avatar_sends)
        runtime._run_battle_tick_once(sock=object())
        new_sends = fake.avatar_sends[before:]

        forced_sends = [s for s in new_sends
                        if s["msgs"][:1] == bytes([emulator.CLIENT_FORCED_POSITION_MSG_ID])]
        self.assertEqual(len(forced_sends), 2)
        forced_size = len(emulator.build_forced_position(
            emulator.PLAYER_VEHICLE_ID, (0.0, 0.0, 0.0), 0.0))
        for send in forced_sends:
            msgs = send["msgs"]
            self.assertFalse(send["reliable"])
            self.assertEqual(len(msgs), forced_size)
            self.assertEqual(msgs[0], emulator.CLIENT_FORCED_POSITION_MSG_ID)
            eid = struct.unpack("<I", msgs[1:5])[0]
            self.assertEqual(eid, emulator.PLAYER_VEHICLE_ID)

    def test_prebattle_session_does_not_emit_forced_position(self):
        fake = _FakeBattleModule()
        sess = _make_session(account_id=30, period_active=False)
        fake.active_battle_accounts[30] = sess
        runtime = EventLoopRuntime(fake)

        runtime._run_battle_tick_once(sock=object())
        sess["battle_last_filter_reset_time"] = -5.0
        runtime._run_battle_tick_once(sock=object())

        forced_sends = [s for s in fake.avatar_sends
                        if s["msgs"][:1] == bytes([emulator.CLIENT_FORCED_POSITION_MSG_ID])]
        self.assertEqual(forced_sends, [])


if __name__ == "__main__":
    unittest.main()
