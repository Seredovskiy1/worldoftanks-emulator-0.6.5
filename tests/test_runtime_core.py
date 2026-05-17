import io
import math
import struct
import threading
import time
import unittest
from unittest import mock

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


def _make_combat_vehicle(health=300, hull=(50.0, 40.0, 35.0),
                         turret=(60.0, 45.0, 40.0)):
    return {
        "name": "TestTank",
        "maxHealth": health,
        "armorModel": {
            "hull": {
                "primaryArmor": list(hull),
                "armorHomogenization": 1.0,
            },
            "turret": {
                "primaryArmor": list(turret),
                "armorHomogenization": 1.0,
            },
            "dimensions": {
                "halfWidth": 2.4,
                "halfLength": 5.2,
                "minHeight": 0.15,
                "hullTop": 2.15,
                "maxHeight": 3.8,
                "centerHeight": 1.3,
            },
        },
        "shells": [],
    }


def _make_shell(kind="ARMOR_PIERCING", penetration=200.0, damage=100.0,
                explosion_radius=0.0, compact=9001, speed=500.0,
                gravity=9.81):
    return {
        "compactDescr": compact,
        "kind": kind,
        "piercingPower": [penetration, penetration],
        "piercingPowerRandomization": 0.0,
        "damage": [damage, damage],
        "damageRandomization": 0.0,
        "normalizationAngle": 0.0,
        "ricochetAngleCos": emulator.SHOT_ARMOR_AUTORICOCHET_COS,
        "explosionRadius": explosion_radius,
        "effectsIndex": 0,
        "speed": speed,
        "gravity": gravity,
    }


def _mock_stone_chunk(model_path=b"content/Environment/Rocks/testStone.model",
                      local=(12.0, 3.0, 34.0), axes=None):
    if axes is None:
        axes = (
            1.0, 0.0, 0.0,
            0.0, 1.0, 0.0,
            0.0, 0.0, 1.0,
        )
    matrix = tuple(float(v) for v in axes) + (
        float(local[0]), float(local[1]), float(local[2]),
    )
    return b"model\x00resource\x00" + model_path + struct.pack("<12f", *matrix)


def _make_combat_session(account_id, team, pos, health=300,
                         vehicle=None):
    sess = _make_session(account_id, period_active=True)
    sess.update({
        "username": f"p{account_id}",
        "battle_team": team,
        "battle_pos": pos,
        "battle_prev_pos": pos,
        "battle_yaw": 0.0,
        "battle_vehicle": vehicle or _make_combat_vehicle(health=health),
        "battle_vehicle_health": health,
        "battle_damage_dealt": 0,
        "battle_damage_received": 0,
        "battle_hits": 0,
        "battle_shots_received": 0,
        "battle_damaged_vehicle_ids": set(),
        "battle_killed_vehicle_ids": set(),
        "battle_frags": 0,
        "battle_bundle_sent": True,
        "battle_period_active": True,
    })
    return sess


def _message_ids(messages):
    out = []
    pos = 0
    while pos + 3 <= len(messages):
        msg_id = messages[pos]
        size = struct.unpack_from("<H", messages, pos + 1)[0]
        out.append(msg_id)
        pos += 3 + size
    return out


def _message_payloads(messages, wanted_id):
    out = []
    pos = 0
    while pos + 3 <= len(messages):
        msg_id = messages[pos]
        size = struct.unpack_from("<H", messages, pos + 1)[0]
        payload = messages[pos + 3:pos + 3 + size]
        if msg_id == wanted_id:
            out.append(payload)
        pos += 3 + size
    return out


def _make_loading_session(account_id, match_id=77, ready=False):
    sess = _make_combat_session(account_id, 1 if account_id % 2 else 2,
                                (0.0, 0.0, float(account_id)))
    sess.update({
        "battle_match_id": match_id,
        "battle_bundle_sent": True,
        "battle_period_active": False,
        "battle_client_ready": ready,
        "avatar_ready_sent": ready,
        "battle_period_timer_started": False,
        "battle_late_period_timer_started": False,
        "battle_generation": 1,
        "battle_start_wall": time.time() - 5.0,
        "battle_ended": False,
    })
    return sess


class RuntimeCoreTests(unittest.TestCase):
    def setUp(self):
        self._active_battle_accounts = dict(emulator.active_battle_accounts)
        self._enable_client_shot_damage_effects = emulator.ENABLE_CLIENT_SHOT_DAMAGE_EFFECTS
        emulator.active_battle_accounts.clear()
        emulator.ENABLE_CLIENT_SHOT_DAMAGE_EFFECTS = True

    def tearDown(self):
        emulator.active_battle_accounts.clear()
        emulator.active_battle_accounts.update(self._active_battle_accounts)
        emulator.ENABLE_CLIENT_SHOT_DAMAGE_EFFECTS = self._enable_client_shot_damage_effects

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

    def test_vehicle_class_tags_identify_artillery_and_tank_destroyers(self):
        vehicles = {v["name"]: v for v in emulator.load_all_vehicles()}
        for name in ("S-51", "SU-18", "M7_Priest", "Hummel"):
            self.assertEqual(vehicles[name]["vehicleClass"], "SPG")
            self.assertTrue(vehicles[name]["isSPG"])
            self.assertTrue(emulator.is_artillery_vehicle(vehicles[name]))
        for name in ("SU-85", "AT-1", "T95"):
            self.assertEqual(vehicles[name]["vehicleClass"], "AT-SPG")
            self.assertTrue(vehicles[name]["isATSPG"])
            self.assertFalse(emulator.is_artillery_vehicle(vehicles[name]))
        self.assertFalse(emulator.is_artillery_vehicle(vehicles["T-34"]))

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

    def test_first_ready_player_schedules_and_starts_without_waiting(self):
        s1 = _make_loading_session(1)
        s2 = _make_loading_session(2)
        emulator.active_battle_accounts[1] = s1
        emulator.active_battle_accounts[2] = s2
        scheduled = []
        sent = []
        arena_updates = []

        def capture_send(_sock, _addr, _sess, msgs, label, reliable=True):
            sent.append((_sess, bytes(msgs), label, reliable))
            return True

        def capture_arena(_sock, _addr, _sess, update_type, data, label):
            arena_updates.append((_sess, update_type, data, label))

        with mock.patch.object(emulator, "send_avatar_messages", side_effect=capture_send), \
                mock.patch.object(emulator, "send_avatar_arena_update", side_effect=capture_arena), \
                mock.patch.object(emulator, "runtime_call_later", side_effect=lambda delay, cb: scheduled.append((delay, cb))), \
                mock.patch.object(emulator, "start_battle_tick_loop", return_value=None):
            emulator.send_avatar_ready_and_prebattle(object(), s1["addr"], s1)
            self.assertEqual(len(scheduled), 1)
            self.assertAlmostEqual(scheduled[0][0],
                                   emulator.BATTLE_READY_GUARD_SECONDS)
            scheduled[0][1]()

        self.assertTrue(s1["battle_client_ready"])
        self.assertFalse(s2["battle_client_ready"])
        self.assertTrue(s1["battle_period_active"])
        self.assertFalse(s2["battle_period_active"])
        self.assertTrue(s1["battle_period_timer_started"])
        self.assertTrue(s2["battle_period_timer_started"])
        self.assertEqual(len(arena_updates), 1)
        ready_updates = []
        for _sess, msgs, _label, _reliable in sent:
            for payload in _message_payloads(
                    msgs, emulator.AVATAR_UPDATEARENA_MSG_ID):
                if payload[4] == emulator.ARENA_UPDATE_AVATAR_READY:
                    ready_updates.append((_sess, payload))
        self.assertEqual(len(ready_updates), 2)
        battle_sends = [entry for entry in sent if "PERIOD=BATTLE" in entry[2]]
        self.assertEqual(len(battle_sends), 1)
        self.assertIs(battle_sends[0][0], s1)

    def test_late_ready_player_enters_running_match(self):
        s1 = _make_loading_session(1, ready=True)
        s2 = _make_loading_session(2)
        s1["battle_period_active"] = True
        s1["battle_period_timer_started"] = True
        s2["battle_period_timer_started"] = True
        emulator.active_battle_accounts[1] = s1
        emulator.active_battle_accounts[2] = s2
        scheduled = []
        sent = []

        def capture_send(_sock, _addr, _sess, msgs, label, reliable=True):
            sent.append((_sess, bytes(msgs), label, reliable))
            return True

        with mock.patch.object(emulator, "send_avatar_messages", side_effect=capture_send), \
                mock.patch.object(emulator, "send_avatar_arena_update", return_value=None), \
                mock.patch.object(emulator, "runtime_call_later", side_effect=lambda delay, cb: scheduled.append((delay, cb))), \
                mock.patch.object(emulator, "start_battle_tick_loop", return_value=None):
            emulator.send_avatar_ready_and_prebattle(object(), s2["addr"], s2)
            self.assertEqual(len(scheduled), 1)
            self.assertAlmostEqual(scheduled[0][0],
                                   emulator.BATTLE_READY_GUARD_SECONDS)
            scheduled[0][1]()

        self.assertTrue(s1["battle_period_active"])
        self.assertTrue(s2["battle_period_active"])
        battle_sends = [entry for entry in sent if "PERIOD=BATTLE" in entry[2]]
        self.assertEqual(len(battle_sends), 1)
        self.assertIs(battle_sends[0][0], s2)

    def test_stale_battle_generation_callback_does_not_start_match(self):
        s1 = _make_loading_session(1, ready=True)
        s2 = _make_loading_session(2, ready=True)
        emulator.active_battle_accounts[1] = s1
        emulator.active_battle_accounts[2] = s2
        scheduled = []
        sent = []

        def capture_send(_sock, _addr, _sess, msgs, label, reliable=True):
            sent.append((_sess, bytes(msgs), label, reliable))
            return True

        with mock.patch.object(emulator, "runtime_call_later", side_effect=lambda delay, cb: scheduled.append((delay, cb))), \
                mock.patch.object(emulator, "send_avatar_messages", side_effect=capture_send), \
                mock.patch.object(emulator, "start_battle_tick_loop", return_value=None):
            emulator.schedule_battle_period(object(), s1["addr"], s1)
            self.assertEqual(len(scheduled), 1)
            s1["battle_generation"] += 1
            scheduled[0][1]()

        self.assertFalse(s1["battle_period_active"])
        self.assertTrue(s2["battle_period_active"])
        battle_sends = [entry for entry in sent if "PERIOD=BATTLE" in entry[2]]
        self.assertEqual(len(battle_sends), 1)
        self.assertIs(battle_sends[0][0], s2)

    def test_repeated_on_client_ready_is_idempotent(self):
        s1 = _make_loading_session(1)
        emulator.active_battle_accounts[1] = s1
        scheduled = []
        sent = []
        arena_updates = []

        def capture_send(_sock, _addr, _sess, msgs, label, reliable=True):
            sent.append((_sess, bytes(msgs), label, reliable))
            return True

        def capture_arena(_sock, _addr, _sess, update_type, data, label):
            arena_updates.append((_sess, update_type, data, label))

        with mock.patch.object(emulator, "send_avatar_messages", side_effect=capture_send), \
                mock.patch.object(emulator, "send_avatar_arena_update", side_effect=capture_arena), \
                mock.patch.object(emulator, "runtime_call_later", side_effect=lambda delay, cb: scheduled.append((delay, cb))):
            emulator.send_avatar_ready_and_prebattle(object(), s1["addr"], s1)
            counts = (len(sent), len(arena_updates), len(scheduled))
            emulator.send_avatar_ready_and_prebattle(object(), s1["addr"], s1)

        self.assertEqual(counts, (len(sent), len(arena_updates), len(scheduled)))

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

    def test_first_motion_does_not_emit_forced_position_by_default(self):
        sess = _make_combat_session(31, 1, emulator.ARENA_SPAWN_POS[emulator.ARENA_TYPE_KARELIA])
        sess["battle_period_active"] = True
        sess["server_vehicle_authoritative"] = True
        sess["battle_forced_position_sent_for_motion"] = False
        sent = []

        def capture_send(_sock, _addr, _sess, msgs, label, reliable=True):
            sent.append((bytes(msgs), label, reliable))
            return True

        payload = b"\x00\x00\x00\x00\x01"
        with mock.patch.object(emulator, "send_avatar_messages",
                               side_effect=capture_send):
            handled = emulator.handle_avatar_base_method(
                object(), sess["addr"], sess, 0xc3, payload)

        self.assertTrue(handled)
        self.assertEqual(sess["battle_motion_flags"], 1)
        forced = [entry for entry in sent
                  if entry[0][:1] == bytes([emulator.CLIENT_FORCED_POSITION_MSG_ID])]
        self.assertEqual(forced, [])

    def test_static_obstacle_chunk_transform_creates_world_obstacle(self):
        ignored = {}
        data = _mock_stone_chunk(local=(12.0, 3.0, 34.0))

        with mock.patch.object(emulator, "find_client_res_file", return_value="stone.model"), \
                mock.patch.object(emulator, "read_model_obstacle_bounds",
                                  return_value=(-4.0, -1.0, 0.0, 4.0, 1.0, 0.0)), \
                mock.patch.object(emulator, "terrain_height_only", return_value=3.1):
            obstacles = emulator.build_static_obstacles_from_chunk_data(
                emulator.ARENA_TYPE_KARELIA, 2, -1, data, ignored)

        self.assertEqual(len(obstacles), 1)
        obstacle = obstacles[0]
        self.assertAlmostEqual(obstacle[0], 212.0)
        self.assertAlmostEqual(obstacle[1], 3.0)
        self.assertAlmostEqual(obstacle[2], -66.0)
        self.assertAlmostEqual(obstacle[3], 2.5)
        self.assertAlmostEqual(obstacle[4], 5.0)
        self.assertEqual(len(obstacle), 7)
        self.assertEqual(ignored, {})

    def test_static_obstacle_text_match_without_transform_is_ignored(self):
        ignored = {}
        data = (b"content/Environment/Rocks/testStone.model" +
                b"not-a-transform")

        obstacles = emulator.build_static_obstacles_from_chunk_data(
            emulator.ARENA_TYPE_KARELIA, 0, 0, data, ignored)

        self.assertEqual(obstacles, [])
        self.assertEqual(ignored.get("transform"), 1)

    def test_static_obstacle_far_from_terrain_is_ignored(self):
        ignored = {}
        data = _mock_stone_chunk(local=(12.0, 3.0, 34.0))

        with mock.patch.object(emulator, "find_client_res_file", return_value="stone.model"), \
                mock.patch.object(emulator, "terrain_height_only", return_value=30.0):
            obstacles = emulator.build_static_obstacles_from_chunk_data(
                emulator.ARENA_TYPE_KARELIA, 0, 0, data, ignored)

        self.assertEqual(obstacles, [])
        self.assertEqual(ignored.get("terrain_y"), 1)

    def test_static_obstacle_config_exclusion_is_ignored(self):
        ignored = {}
        data = _mock_stone_chunk(
            model_path=b"content/Environment/Rocks/testStone.model",
            local=(12.0, 3.0, 34.0))
        original = emulator.CONFIG.get("maps", {}).get("static_obstacle_exclusions")
        try:
            emulator.CONFIG.setdefault("maps", {})["static_obstacle_exclusions"] = {
                "1": [
                    {
                        "center": [12.0, 34.0],
                        "radius": 2.0,
                        "model": "teststone.model",
                    }
                ]
            }
            with mock.patch.object(emulator, "find_client_res_file", return_value="stone.model"):
                obstacles = emulator.build_static_obstacles_from_chunk_data(
                    emulator.ARENA_TYPE_KARELIA, 0, 0, data, ignored)
        finally:
            if original is None:
                emulator.CONFIG.setdefault("maps", {}).pop("static_obstacle_exclusions", None)
            else:
                emulator.CONFIG.setdefault("maps", {})["static_obstacle_exclusions"] = original

        self.assertEqual(obstacles, [])
        self.assertEqual(ignored.get("excluded"), 1)

    def test_static_obstacle_blocks_motion_only_at_valid_position(self):
        original_cache = dict(emulator.STATIC_OBSTACLE_CACHE)
        try:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE[emulator.ARENA_TYPE_KARELIA] = [
                (10.0, 0.0, 10.0, 3.0, 4.0, b"content/Environment/Rocks/testStone.model")
            ]

            blocked = emulator.find_blocking_static_obstacle(
                emulator.ARENA_TYPE_KARELIA, 10.5, 10.0, tank_radius=2.5)
            clear = emulator.find_blocking_static_obstacle(
                emulator.ARENA_TYPE_KARELIA, 30.0, 30.0, tank_radius=2.5)
            new_x, new_z, was_blocked = emulator.resolve_motion_against_obstacles(
                emulator.ARENA_TYPE_KARELIA, 0.0, 0.0, 10.5, 10.0,
                tank_radius=2.5)
        finally:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE.update(original_cache)

        self.assertIsNotNone(blocked)
        self.assertIsNone(clear)
        self.assertTrue(was_blocked)
        self.assertEqual((new_x, new_z), (10.5, 0.0))

    def test_static_obstacle_scaled_down_uses_footprint(self):
        original_cache = dict(emulator.STATIC_OBSTACLE_CACHE)
        try:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE[emulator.ARENA_TYPE_KARELIA] = [
                (0.0, 0.0, 0.0, 5.0, 10.0,
                 b"content/Environment/Rocks/testStone.model",
                 ((-2.0, -2.0), (2.0, -2.0), (2.0, 2.0), (-2.0, 2.0)))
            ]

            clear = emulator.find_blocking_static_obstacle(
                emulator.ARENA_TYPE_KARELIA, 6.0, 0.0, tank_radius=0.5)
            blocked = emulator.find_blocking_static_obstacle(
                emulator.ARENA_TYPE_KARELIA, 2.2, 0.0, tank_radius=0.5)
        finally:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE.update(original_cache)

        self.assertIsNone(clear)
        self.assertIsNotNone(blocked)

    def test_static_obstacle_generated_footprint_is_shrunk_to_visible_silhouette(self):
        ignored = {}
        data = _mock_stone_chunk(local=(0.0, 3.0, 0.0))
        original_cache = dict(emulator.STATIC_OBSTACLE_CACHE)
        original_shrink = emulator.STATIC_OBSTACLE_FOOTPRINT_SHRINK
        try:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_FOOTPRINT_SHRINK = 0.90
            with mock.patch.object(emulator, "find_client_res_file", return_value="stone.model"), \
                    mock.patch.object(emulator, "read_model_obstacle_bounds",
                                      return_value=(-4.0, -1.0, -4.0, 4.0, 1.0, 4.0)), \
                    mock.patch.object(emulator, "terrain_height_only", return_value=3.0):
                obstacles = emulator.build_static_obstacles_from_chunk_data(
                    emulator.ARENA_TYPE_KARELIA, 0, 0, data, ignored)
            emulator.STATIC_OBSTACLE_CACHE[emulator.ARENA_TYPE_KARELIA] = obstacles
            blocked = emulator.find_blocking_static_obstacle(
                emulator.ARENA_TYPE_KARELIA, 3.5, 0.0, tank_radius=0.1)
            clear = emulator.find_blocking_static_obstacle(
                emulator.ARENA_TYPE_KARELIA, 3.8, 0.0, tank_radius=0.1)
        finally:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE.update(original_cache)
            emulator.STATIC_OBSTACLE_FOOTPRINT_SHRINK = original_shrink

        self.assertEqual(ignored, {})
        self.assertEqual(len(obstacles), 1)
        footprint = obstacles[0][6]
        self.assertIsNotNone(footprint)
        xs = [x for x, _ in footprint]
        zs = [z for _, z in footprint]
        self.assertAlmostEqual(max(xs), 3.6, places=4)
        self.assertAlmostEqual(min(xs), -3.6, places=4)
        self.assertAlmostEqual(max(zs), 3.6, places=4)
        self.assertAlmostEqual(min(zs), -3.6, places=4)
        self.assertIsNotNone(blocked)
        self.assertIsNone(clear)

    def test_static_obstacle_scaled_up_uses_footprint(self):
        original_cache = dict(emulator.STATIC_OBSTACLE_CACHE)
        try:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE[emulator.ARENA_TYPE_KARELIA] = [
                (0.0, 0.0, 0.0, 2.0, 4.0,
                 b"content/Environment/Rocks/testStone.model",
                 ((-8.0, -1.0), (8.0, -1.0), (8.0, 1.0), (-8.0, 1.0)))
            ]

            blocked = emulator.find_blocking_static_obstacle(
                emulator.ARENA_TYPE_KARELIA, 6.5, 0.0, tank_radius=0.5)
            clear = emulator.find_blocking_static_obstacle(
                emulator.ARENA_TYPE_KARELIA, 9.0, 0.0, tank_radius=0.5)
        finally:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE.update(original_cache)

        self.assertIsNotNone(blocked)
        self.assertIsNone(clear)

    def test_static_obstacle_swept_path_blocks_tunneling(self):
        original_cache = dict(emulator.STATIC_OBSTACLE_CACHE)
        try:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE[emulator.ARENA_TYPE_KARELIA] = [
                (0.0, 0.0, 0.0, 2.0, 4.0,
                 b"content/Environment/Rocks/testStone.model",
                 ((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0)))
            ]

            blocked = emulator.find_blocking_static_obstacle_on_path(
                emulator.ARENA_TYPE_KARELIA, -5.0, 0.0, 5.0, 0.0,
                tank_radius=0.1)
            new_x, new_z, was_blocked = emulator.resolve_motion_against_obstacles(
                emulator.ARENA_TYPE_KARELIA, -5.0, 0.0, 5.0, 0.0,
                tank_radius=0.1)
        finally:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE.update(original_cache)

        self.assertIsNotNone(blocked)
        self.assertTrue(was_blocked)
        self.assertEqual((new_x, new_z), (-5.0, 0.0))

    def test_static_obstacle_swept_path_allows_axis_slide(self):
        original_cache = dict(emulator.STATIC_OBSTACLE_CACHE)
        try:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE[emulator.ARENA_TYPE_KARELIA] = [
                (0.0, 0.0, 0.0, 2.0, 4.0,
                 b"content/Environment/Rocks/testStone.model",
                 ((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0)))
            ]

            new_x, new_z, was_blocked = emulator.resolve_motion_against_obstacles(
                emulator.ARENA_TYPE_KARELIA, -5.0, 5.0, 0.0, 0.0,
                tank_radius=0.1)
        finally:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE.update(original_cache)

        self.assertTrue(was_blocked)
        self.assertEqual((new_x, new_z), (0.0, 5.0))

    def test_static_obstacle_rotated_asymmetric_footprint_is_shifted(self):
        data = _mock_stone_chunk(
            local=(10.0, 3.0, 20.0),
            axes=(
                0.0, 0.0, 1.0,
                0.0, 1.0, 0.0,
                -1.0, 0.0, 0.0,
            ))
        bounds = (2.0, -1.0, -1.0, 6.0, 1.0, 1.0)
        original_cache = dict(emulator.STATIC_OBSTACLE_CACHE)
        try:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            with mock.patch.object(emulator, "find_client_res_file", return_value="stone.model"), \
                    mock.patch.object(emulator, "read_model_obstacle_bounds", return_value=bounds), \
                    mock.patch.object(emulator, "terrain_height_only", return_value=3.0):
                obstacles = emulator.build_static_obstacles_from_chunk_data(
                    emulator.ARENA_TYPE_KARELIA, 0, 0, data, {})
            emulator.STATIC_OBSTACLE_CACHE[emulator.ARENA_TYPE_KARELIA] = obstacles
            shifted = emulator.find_blocking_static_obstacle(
                emulator.ARENA_TYPE_KARELIA, 10.0, 22.5, tank_radius=0.1)
            raw_center = emulator.find_blocking_static_obstacle(
                emulator.ARENA_TYPE_KARELIA, 10.0, 20.0, tank_radius=0.1)
        finally:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE.update(original_cache)

        self.assertEqual(len(obstacles), 1)
        self.assertIsNotNone(shifted)
        self.assertIsNone(raw_center)

    def test_static_obstacle_blocks_shot_only_when_validated(self):
        original_cache = dict(emulator.STATIC_OBSTACLE_CACHE)
        source = _make_combat_session(81, 1, (0.0, 0.0, 0.0))
        source["battle_arena_type_id"] = emulator.ARENA_TYPE_KARELIA
        try:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE[emulator.ARENA_TYPE_KARELIA] = [
                (0.0, 1.0, 20.0, 3.0, 4.0, b"content/Environment/Rocks/testStone.model")
            ]
            hit = emulator.ray_static_obstacle_hit(
                source, (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), 40.0,
                shooter_gap=0.0, target_gap=0.0)
            emulator.STATIC_OBSTACLE_CACHE[emulator.ARENA_TYPE_KARELIA] = []
            miss = emulator.ray_static_obstacle_hit(
                source, (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), 40.0,
                shooter_gap=0.0, target_gap=0.0)
        finally:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE.update(original_cache)

        self.assertIsNotNone(hit)
        self.assertIsNone(miss)

    def test_static_obstacle_index_skips_far_obstacles(self):
        original_cache = dict(emulator.STATIC_OBSTACLE_CACHE)
        original_index = dict(emulator.STATIC_OBSTACLE_INDEX_CACHE)
        far_obstacle = (
            500.0, 0.0, 500.0, 2.0, 4.0,
            b"content/Environment/Rocks/far.model",
            ((499.0, 499.0), (501.0, 499.0), (501.0, 501.0), (499.0, 501.0)),
        )
        near_obstacle = (
            10.0, 0.0, 10.0, 2.0, 4.0,
            b"content/Environment/Rocks/near.model",
            ((9.0, 9.0), (11.0, 9.0), (11.0, 11.0), (9.0, 11.0)),
        )
        try:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_INDEX_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE[emulator.ARENA_TYPE_KARELIA] = [
                far_obstacle, near_obstacle,
            ]
            near_results = list(emulator.iter_obstacles_near_point(
                emulator.ARENA_TYPE_KARELIA, 10.0, 10.0, halo=2.0))
            far_results = list(emulator.iter_obstacles_near_point(
                emulator.ARENA_TYPE_KARELIA, 500.0, 500.0, halo=2.0))
            empty_results = list(emulator.iter_obstacles_near_point(
                emulator.ARENA_TYPE_KARELIA, 250.0, 250.0, halo=2.0))
        finally:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE.update(original_cache)
            emulator.STATIC_OBSTACLE_INDEX_CACHE.clear()
            emulator.STATIC_OBSTACLE_INDEX_CACHE.update(original_index)

        self.assertEqual(len(near_results), 1)
        self.assertIs(near_results[0], near_obstacle)
        self.assertEqual(len(far_results), 1)
        self.assertIs(far_results[0], far_obstacle)
        self.assertEqual(empty_results, [])

    def test_static_obstacle_default_halo_uses_configured_value(self):
        original_cache = dict(emulator.STATIC_OBSTACLE_CACHE)
        original_index = dict(emulator.STATIC_OBSTACLE_INDEX_CACHE)
        obstacle = (
            0.0, 0.0, 0.0, 1.0, 2.0,
            b"content/Environment/Rocks/small.model",
            ((-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)),
        )
        try:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_INDEX_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE[emulator.ARENA_TYPE_KARELIA] = [obstacle]
            clear = emulator.find_blocking_static_obstacle(
                emulator.ARENA_TYPE_KARELIA, 2.0, 0.0)
            blocked = emulator.find_blocking_static_obstacle(
                emulator.ARENA_TYPE_KARELIA, 1.5, 0.0)
        finally:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE.update(original_cache)
            emulator.STATIC_OBSTACLE_INDEX_CACHE.clear()
            emulator.STATIC_OBSTACLE_INDEX_CACHE.update(original_index)

        self.assertIsNone(clear)
        self.assertIsNotNone(blocked)

    def test_packed_section_bounds_parser_reads_min_max(self):
        polygon_bounds = (-3.5, -1.0, -4.25, 3.5, 2.5, 4.25)
        min_vec = polygon_bounds[:3]
        max_vec = polygon_bounds[3:]
        header = b'\x45\x4e\xa1\x62\x00'
        strings = b'boundingBox\x00min\x00max\x00\x00'
        type_section = emulator._PACKED_SECTION_TYPE_SECTION
        type_float = emulator._PACKED_SECTION_TYPE_FLOAT
        shift = emulator._PACKED_SECTION_TYPE_SHIFT

        bb_children = struct.pack(
            '<iHiHi',
            (type_section << shift) | 0,
            1,
            (type_float << shift) | 12,
            2,
            (type_float << shift) | 24,
        )
        bb_section = (struct.pack('<h', 2) + bb_children +
                      struct.pack('<3f', *min_vec) +
                      struct.pack('<3f', *max_vec))
        root_children = struct.pack(
            '<iHi',
            (type_section << shift) | 0,
            0,
            (type_section << shift) | len(bb_section),
        )
        root_section = struct.pack('<h', 1) + root_children + bb_section
        data = header + strings + root_section
        bounds = emulator._packed_section_find_bounds(data)
        self.assertIsNotNone(bounds)
        for actual, expected in zip(bounds, polygon_bounds):
            self.assertAlmostEqual(actual, expected, places=4)

    def test_static_obstacles_load_for_non_karelia_arena(self):
        data = _mock_stone_chunk(local=(12.0, 3.0, 34.0))
        original_cache = dict(emulator.STATIC_OBSTACLE_CACHE)

        def fake_open(_path, _mode="rb"):
            return io.BytesIO(data)

        try:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            with mock.patch.object(emulator, "find_client_space_dir", return_value="space"), \
                    mock.patch.object(emulator.os, "listdir", return_value=["00010002o.chunk"]), \
                    mock.patch("builtins.open", side_effect=fake_open), \
                    mock.patch.object(emulator, "find_client_res_file", return_value="stone.model"), \
                    mock.patch.object(emulator, "read_model_obstacle_bounds",
                                      return_value=(-4.0, -1.0, 0.0, 4.0, 1.0, 0.0)), \
                    mock.patch.object(emulator, "terrain_height_only", return_value=3.0):
                obstacles = emulator.load_static_obstacles_for_arena(2)
        finally:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE.update(original_cache)

        self.assertEqual(len(obstacles), 1)
        self.assertAlmostEqual(obstacles[0][0], 112.0)
        self.assertAlmostEqual(obstacles[0][2], 234.0)

    def test_static_obstacle_cache_lookup_skips_disk_when_prewarmed(self):
        original_cache = dict(emulator.STATIC_OBSTACLE_CACHE)
        sentinel_obstacles = [
            (1.0, 0.0, 2.0, 3.0, 4.0, b"prewarmed/sentinel.model")
        ]
        try:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE[emulator.ARENA_TYPE_KARELIA] = sentinel_obstacles

            def _explode(*_args, **_kwargs):
                raise AssertionError(
                    "load_static_obstacles_for_arena must not touch disk when cache is warm")

            with mock.patch.object(emulator, "find_client_space_dir",
                                   side_effect=_explode), \
                    mock.patch.object(emulator.os, "listdir",
                                      side_effect=_explode), \
                    mock.patch("builtins.open", side_effect=_explode):
                result = emulator.load_static_obstacles_for_arena(
                    emulator.ARENA_TYPE_KARELIA)
        finally:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE.update(original_cache)

        self.assertIs(result, sentinel_obstacles)

    def test_startup_prewarm_loads_enabled_static_obstacles(self):
        original_cache = dict(emulator.STATIC_OBSTACLE_CACHE)
        original_enabled = emulator.ENABLED_ARENA_TYPE_IDS
        calls = []

        def fake_load(arena_type_id):
            calls.append(arena_type_id)
            return [(float(arena_type_id), 0.0, 0.0, 1.0, 1.0, b"stone.model")]

        try:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.ENABLED_ARENA_TYPE_IDS = (1, 2)
            with mock.patch.object(emulator, "load_static_obstacles_for_arena",
                                   side_effect=fake_load):
                warmed = emulator.prewarm_static_obstacles_for_enabled_maps()
        finally:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE.update(original_cache)
            emulator.ENABLED_ARENA_TYPE_IDS = original_enabled

        self.assertEqual(calls, [1, 2])
        self.assertEqual(warmed, {1: 1, 2: 1})

    def test_startup_prewarm_skips_warm_static_obstacles(self):
        original_cache = dict(emulator.STATIC_OBSTACLE_CACHE)
        original_enabled = emulator.ENABLED_ARENA_TYPE_IDS
        sentinel = [(1.0, 0.0, 0.0, 1.0, 1.0, b"stone.model")]
        try:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE[emulator.ARENA_TYPE_KARELIA] = sentinel
            emulator.ENABLED_ARENA_TYPE_IDS = (emulator.ARENA_TYPE_KARELIA,)
            with mock.patch.object(emulator, "load_static_obstacles_for_arena",
                                   side_effect=AssertionError("must stay warm")):
                warmed = emulator.prewarm_static_obstacles_for_enabled_maps()
        finally:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE.update(original_cache)
            emulator.ENABLED_ARENA_TYPE_IDS = original_enabled

        self.assertEqual(warmed, {emulator.ARENA_TYPE_KARELIA: 1})

    def test_matchmaker_launch_does_not_cold_load_static_obstacles(self):
        original_queue = list(emulator.matchmaking_queue)
        original_timer = emulator.matchmaking_timer
        original_next = emulator.next_battle_id
        original_cache = dict(emulator.STATIC_OBSTACLE_CACHE)
        sess = _make_combat_session(301, 1, (0.0, 0.0, 0.0))
        sess["queued_for_battle"] = True
        sess["battle_arena_type_id"] = emulator.ARENA_TYPE_KARELIA
        scheduled = []
        try:
            emulator.matchmaking_queue[:] = [{"addr": sess["addr"], "sess": sess}]
            emulator.matchmaking_timer = None
            emulator.STATIC_OBSTACLE_CACHE.clear()
            with mock.patch.object(emulator.random, "uniform", return_value=0.0), \
                    mock.patch.object(emulator, "runtime_call_later",
                                      side_effect=lambda delay, cb: scheduled.append((delay, cb))), \
                    mock.patch.object(emulator, "load_static_obstacles_for_arena",
                                      side_effect=AssertionError("must not cold load")), \
                    mock.patch.object(emulator, "send_account_event", return_value=None), \
                    mock.patch.object(emulator, "send_avatar_player", return_value=None):
                emulator.start_matchmaking_timer(object())
                self.assertEqual(len(scheduled), 1)
                scheduled[0][1]()
        finally:
            emulator.matchmaking_queue[:] = original_queue
            emulator.matchmaking_timer = original_timer
            emulator.next_battle_id = original_next
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE.update(original_cache)

        self.assertFalse(sess.get("queued_for_battle"))
        self.assertIn("battle_capture_state", sess)

    def test_matchmaker_launch_uses_warm_static_obstacle_cache(self):
        original_queue = list(emulator.matchmaking_queue)
        original_timer = emulator.matchmaking_timer
        original_next = emulator.next_battle_id
        original_cache = dict(emulator.STATIC_OBSTACLE_CACHE)
        sess = _make_combat_session(302, 1, (0.0, 0.0, 0.0))
        sess["queued_for_battle"] = True
        sess["battle_arena_type_id"] = emulator.ARENA_TYPE_KARELIA
        scheduled = []
        try:
            emulator.matchmaking_queue[:] = [{"addr": sess["addr"], "sess": sess}]
            emulator.matchmaking_timer = None
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE[emulator.ARENA_TYPE_KARELIA] = [
                (1.0, 0.0, 0.0, 1.0, 1.0, b"stone.model")
            ]
            with mock.patch.object(emulator.random, "uniform", return_value=0.0), \
                    mock.patch.object(emulator, "runtime_call_later",
                                      side_effect=lambda delay, cb: scheduled.append((delay, cb))), \
                    mock.patch.object(emulator, "load_static_obstacles_for_arena",
                                      side_effect=AssertionError("must use warm cache")), \
                    mock.patch.object(emulator, "send_account_event", return_value=None), \
                    mock.patch.object(emulator, "send_avatar_player", return_value=None):
                emulator.start_matchmaking_timer(object())
                self.assertEqual(len(scheduled), 1)
                scheduled[0][1]()
        finally:
            emulator.matchmaking_queue[:] = original_queue
            emulator.matchmaking_timer = original_timer
            emulator.next_battle_id = original_next
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE.update(original_cache)

        self.assertFalse(sess.get("queued_for_battle"))
        self.assertIn("battle_capture_state", sess)

    def test_artillery_direct_hit_uses_marker_target(self):
        artillery = _make_combat_vehicle()
        artillery.update({
            "vehicleClass": "SPG",
            "isSPG": True,
            "tags": frozenset(("SPG",)),
        })
        source = _make_combat_session(91, 1, (0.0, 0.0, 40.0),
                                      vehicle=artillery)
        target = _make_combat_session(92, 2, (0.0, 0.0, 20.0), health=300)
        shell = _make_shell(kind="HIGH_EXPLOSIVE", penetration=300.0,
                            damage=120.0, explosion_radius=6.0)
        source["battle_last_shot_shell"] = shell
        source["battle_last_shot_target_pos"] = (0.0, 1.3, 20.0)
        source["battle_last_shot_target_pos_time"] = time.time()
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[target["account_id"]] = target

        with mock.patch.object(emulator, "send_avatar_messages", return_value=True), \
                mock.patch.object(emulator, "send_remote_vehicle", return_value=None):
            hit_target, damage, resolved = emulator.apply_shot_damage(
                object(), source, shell, (0.0, 2.0, 40.0),
                emulator.normalize_vec((0.0, -0.2, -1.0)))

        self.assertIs(hit_target, target)
        self.assertEqual(damage, 120)
        self.assertEqual(resolved["result"], emulator.SHOT_RESULT_ARMOR_PIERCED)
        self.assertTrue(resolved["artilleryDirectHit"])
        self.assertEqual(target["battle_vehicle_health"], 180)

    def test_artillery_direct_he_nonpenetration_uses_direct_damage(self):
        artillery = _make_combat_vehicle()
        artillery.update({
            "vehicleClass": "SPG",
            "isSPG": True,
            "tags": frozenset(("SPG",)),
        })
        source = _make_combat_session(191, 1, (0.0, 0.0, 40.0),
                                      vehicle=artillery)
        target = _make_combat_session(
            192, 2, (0.0, 0.0, 20.0), health=3200,
            vehicle=_make_combat_vehicle(health=3200, hull=(200.0, 185.0, 160.0)))
        shell = _make_shell(kind="HIGH_EXPLOSIVE", penetration=80.0,
                            damage=1000.0, explosion_radius=10.0)
        source["battle_last_shot_shell"] = shell
        source["battle_last_shot_target_pos"] = (0.0, 1.3, 20.0)
        source["battle_last_shot_target_pos_time"] = time.time()
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[target["account_id"]] = target

        with mock.patch.object(emulator, "send_avatar_messages", return_value=True), \
                mock.patch.object(emulator, "send_remote_vehicle", return_value=None):
            hit_target, damage, resolved = emulator.apply_shot_damage(
                object(), source, shell, (0.0, 2.0, 40.0),
                emulator.normalize_vec((0.0, -0.2, -1.0)))

        self.assertIs(hit_target, target)
        self.assertEqual(damage, 550)
        self.assertEqual(resolved["result"], emulator.SHOT_RESULT_CRITICAL_HIT)
        self.assertTrue(resolved["artilleryDirectHit"])
        self.assertEqual(target["battle_vehicle_health"], 2650)

    def test_artillery_he_splash_deals_scaled_damage_near_marker(self):
        artillery = _make_combat_vehicle()
        artillery.update({
            "vehicleClass": "SPG",
            "isSPG": True,
            "tags": frozenset(("SPG",)),
        })
        source = _make_combat_session(93, 1, (0.0, 0.0, 40.0),
                                      vehicle=artillery)
        target = _make_combat_session(94, 2, (0.0, 0.0, 20.0), health=300)
        shell = _make_shell(kind="HIGH_EXPLOSIVE", penetration=5.0,
                            damage=100.0, explosion_radius=10.0)
        source["battle_last_shot_shell"] = shell
        source["battle_last_shot_target_pos"] = (4.0, 1.3, 20.0)
        source["battle_last_shot_target_pos_time"] = time.time()
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[target["account_id"]] = target

        with mock.patch.object(emulator, "send_avatar_messages", return_value=True), \
                mock.patch.object(emulator, "send_remote_vehicle", return_value=None):
            hit_target, damage, resolved = emulator.apply_shot_damage(
                object(), source, shell, (0.0, 2.0, 40.0),
                emulator.normalize_vec((0.2, -0.2, -1.0)))

        self.assertIs(hit_target, target)
        self.assertEqual(damage, 86)
        self.assertTrue(resolved["artillerySplash"])
        self.assertEqual(resolved["result"], emulator.SHOT_RESULT_CRITICAL_HIT)
        self.assertEqual(target["battle_vehicle_health"], 214)

    def test_artillery_he_splash_misses_outside_radius(self):
        artillery = _make_combat_vehicle()
        artillery.update({
            "vehicleClass": "SPG",
            "isSPG": True,
            "tags": frozenset(("SPG",)),
        })
        source = _make_combat_session(95, 1, (0.0, 0.0, 40.0),
                                      vehicle=artillery)
        target = _make_combat_session(96, 2, (0.0, 0.0, 20.0), health=300)
        shell = _make_shell(kind="HIGH_EXPLOSIVE", penetration=5.0,
                            damage=100.0, explosion_radius=5.0)
        source["battle_last_shot_shell"] = shell
        source["battle_last_shot_target_pos"] = (12.0, 1.3, 20.0)
        source["battle_last_shot_target_pos_time"] = time.time()
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[target["account_id"]] = target

        hit_target, damage, resolved = emulator.apply_shot_damage(
            object(), source, shell, (0.0, 2.0, 40.0),
            emulator.normalize_vec((0.2, -0.2, -1.0)))

        self.assertIsNone(hit_target)
        self.assertEqual(damage, 0)
        self.assertIsNone(resolved)
        self.assertEqual(target["battle_vehicle_health"], 300)

    def test_artillery_shot_delays_impact_after_tracer(self):
        shell = _make_shell(kind="HIGH_EXPLOSIVE", penetration=5.0,
                            damage=100.0, explosion_radius=10.0,
                            compact=9001, speed=120.0, gravity=160.0)
        artillery = _make_combat_vehicle()
        artillery.update({
            "vehicleClass": "SPG",
            "isSPG": True,
            "tags": frozenset(("SPG",)),
            "reloadTime": 5.0,
            "defaultAmmo": [9001, 3],
            "shells": [shell],
        })
        source = _make_combat_session(97, 1, (0.0, 0.0, 40.0),
                                      vehicle=artillery)
        target = _make_combat_session(98, 2, (0.0, 0.0, 20.0), health=300)
        source["battle_current_shell"] = 9001
        source["battle_next_shell"] = 9001
        source["battle_ammo_stock"] = {9001: 3}
        source["battle_target_pos"] = (4.0, 1.3, 20.0)
        source["battle_target_pos_time"] = time.time()
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[target["account_id"]] = target
        sent_messages = []
        scheduled = []

        def capture_send(_sock, _addr, _sess, msgs, _label, reliable=True):
            sent_messages.append(bytes(msgs))
            return True

        def capture_later(delay, callback):
            scheduled.append((delay, callback))
            return None

        with mock.patch.object(emulator, "send_avatar_messages", side_effect=capture_send), \
                mock.patch.object(emulator, "broadcast_remote_vehicle_shot", return_value=None), \
                mock.patch.object(emulator, "send_remote_vehicle", return_value=None), \
                mock.patch.object(emulator, "runtime_call_later", side_effect=capture_later):
            emulator.handle_vehicle_shot(object(), source["addr"], source)

        immediate_ids = []
        show_tracers = []
        for messages in sent_messages:
            immediate_ids.extend(_message_ids(messages))
            show_tracers.extend(_message_payloads(
                messages, emulator.AVATAR_SHOW_TRACER_MSG_ID))
        self.assertIn(emulator.VEHICLE_SHOW_SHOOTING_MSG_ID, immediate_ids)
        self.assertIn(emulator.AVATAR_SHOW_TRACER_MSG_ID, immediate_ids)
        self.assertNotIn(emulator.AVATAR_STOP_TRACER_MSG_ID, immediate_ids)
        self.assertNotIn(emulator.AVATAR_EXPLODE_PROJECTILE_MSG_ID,
                         immediate_ids)
        self.assertEqual(len(show_tracers), 1)
        tracer = show_tracers[0]
        self.assertEqual(struct.unpack_from("<I", tracer, 4)[0], 0)
        visual_shot_id = struct.unpack_from("<f", tracer, 8)[0]
        tracer_start = struct.unpack_from("<fff", tracer, 13)
        tracer_velocity = struct.unpack_from("<fff", tracer, 25)
        tracer_gravity = struct.unpack_from("<f", tracer, 37)[0]
        marker = source["battle_target_pos"]
        tracer_marker_distance = math.sqrt(
            (tracer_start[0] - marker[0]) ** 2 +
            (tracer_start[2] - marker[2]) ** 2)
        self.assertAlmostEqual(tracer_marker_distance,
                               emulator.ARTILLERY_VISIBLE_TRACER_BACK_DISTANCE,
                               places=4)
        self.assertGreater(tracer_start[1],
                           marker[1] + emulator.ARTILLERY_VISIBLE_TRACER_HEIGHT - 0.1)
        self.assertLess(tracer_velocity[1], 0.0)
        self.assertGreaterEqual(tracer_gravity, 12.0)
        self.assertEqual(target["battle_vehicle_health"], 300)
        self.assertGreaterEqual(scheduled[0][0], emulator.ARTILLERY_FLIGHT_TIME_MIN)
        self.assertAlmostEqual(scheduled[0][0],
                               emulator.ARTILLERY_VISIBLE_TRACER_TIME)

        before_callback = len(sent_messages)
        with mock.patch.object(emulator, "send_avatar_messages", side_effect=capture_send), \
                mock.patch.object(emulator, "send_remote_vehicle", return_value=None):
            scheduled[0][1]()
        delayed_ids = []
        stop_tracers = []
        explosions = []
        for messages in sent_messages[before_callback:]:
            delayed_ids.extend(_message_ids(messages))
            stop_tracers.extend(_message_payloads(
                messages, emulator.AVATAR_STOP_TRACER_MSG_ID))
            explosions.extend(_message_payloads(
                messages, emulator.AVATAR_EXPLODE_PROJECTILE_MSG_ID))
        self.assertIn(emulator.AVATAR_STOP_TRACER_MSG_ID, delayed_ids)
        self.assertIn(emulator.AVATAR_EXPLODE_PROJECTILE_MSG_ID, delayed_ids)
        self.assertEqual(struct.unpack_from("<f", stop_tracers[0], 4)[0],
                         visual_shot_id)
        self.assertEqual(struct.unpack_from("<f", explosions[0], 4)[0],
                         visual_shot_id)
        self.assertEqual(target["battle_vehicle_health"], 214)

    def test_artillery_visible_tracer_keeps_nonzero_x_velocity(self):
        shell = _make_shell(kind="HIGH_EXPLOSIVE", compact=9101,
                            speed=120.0, gravity=160.0)
        artillery = _make_combat_vehicle()
        artillery.update({
            "vehicleClass": "SPG",
            "isSPG": True,
            "tags": frozenset(("SPG",)),
            "defaultAmmo": [9101, 1],
            "shells": [shell],
        })
        source = _make_combat_session(171, 1, (0.0, 0.0, 40.0),
                                      vehicle=artillery)
        source["battle_current_shell"] = 9101
        source["battle_ammo_stock"] = {9101: 1}
        source["battle_target_pos"] = (0.0, 1.3, 20.0)
        source["battle_target_pos_time"] = time.time()

        msgs, _reload_time, _shell_cd, fired = emulator.build_vehicle_shot_messages(source)

        self.assertTrue(fired)
        show_tracers = _message_payloads(msgs, emulator.AVATAR_SHOW_TRACER_MSG_ID)
        self.assertEqual(len(show_tracers), 1)
        tracer = show_tracers[0]
        self.assertEqual(struct.unpack_from("<I", tracer, 4)[0], 0)
        tracer_velocity = struct.unpack_from("<fff", tracer, 25)
        self.assertGreaterEqual(abs(tracer_velocity[0]),
                                emulator.ARTILLERY_VISIBLE_TRACER_MIN_VX)
        server_velocity = source["battle_last_server_shot_info"][2]
        self.assertAlmostEqual(server_velocity[0], 0.0, places=5)
        self.assertEqual(source["battle_last_visual_flight_time"],
                         emulator.ARTILLERY_VISIBLE_TRACER_TIME)

    def test_non_artillery_tracer_keeps_player_vehicle_id(self):
        shell = _make_shell(compact=9102, speed=120.0, gravity=9.81)
        tank = _make_combat_vehicle()
        tank.update({
            "defaultAmmo": [9102, 1],
            "shells": [shell],
        })
        source = _make_combat_session(172, 1, (0.0, 0.0, 40.0),
                                      vehicle=tank)
        source["battle_current_shell"] = 9102
        source["battle_ammo_stock"] = {9102: 1}
        source["battle_target_pos"] = (0.0, 1.3, 20.0)
        source["battle_target_pos_time"] = time.time()

        msgs, _reload_time, _shell_cd, fired = emulator.build_vehicle_shot_messages(source)

        self.assertTrue(fired)
        show_tracers = _message_payloads(msgs, emulator.AVATAR_SHOW_TRACER_MSG_ID)
        self.assertEqual(len(show_tracers), 1)
        tracer = show_tracers[0]
        self.assertEqual(struct.unpack_from("<I", tracer, 4)[0],
                         emulator.PLAYER_VEHICLE_ID)
        tracer_velocity = struct.unpack_from("<fff", tracer, 25)
        self.assertAlmostEqual(tracer_velocity[0], 0.0, places=5)
        self.assertIsNone(source["battle_last_visual_flight_time"])

    def test_visual_shot_ids_are_unique_across_players(self):
        shell = _make_shell(compact=9103, speed=120.0, gravity=9.81)
        tank = _make_combat_vehicle()
        tank.update({
            "defaultAmmo": [9103, 1],
            "shells": [shell],
        })
        source_a = _make_combat_session(173, 1, (0.0, 0.0, 40.0),
                                        vehicle=tank)
        source_b = _make_combat_session(174, 2, (0.0, 0.0, -40.0),
                                        vehicle=tank)
        for source in (source_a, source_b):
            source["battle_current_shell"] = 9103
            source["battle_ammo_stock"] = {9103: 1}
            source["battle_target_pos"] = (0.0, 1.3, 0.0)
            source["battle_target_pos_time"] = time.time()
            source["battle_shot_id"] = 1

        msgs_a, _reload_a, _shell_a, fired_a = emulator.build_vehicle_shot_messages(source_a)
        msgs_b, _reload_b, _shell_b, fired_b = emulator.build_vehicle_shot_messages(source_b)

        self.assertTrue(fired_a)
        self.assertTrue(fired_b)
        shot_a = struct.unpack_from(
            "<f",
            _message_payloads(msgs_a, emulator.AVATAR_SHOW_TRACER_MSG_ID)[0],
            8)[0]
        shot_b = struct.unpack_from(
            "<f",
            _message_payloads(msgs_b, emulator.AVATAR_SHOW_TRACER_MSG_ID)[0],
            8)[0]
        self.assertNotEqual(shot_a, shot_b)

    def test_known_remote_shot_bundles_shooting_before_tracer(self):
        source = _make_combat_session(171, 1, (0.0, 0.0, 40.0))
        observer = _make_combat_session(172, 2, (0.0, 0.0, 20.0))
        observer["known_remote_accounts"].add(source["account_id"])
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[observer["account_id"]] = observer
        sent = []
        scheduled = []

        def capture_send(_sock, _addr, _sess, msgs, _label, reliable=True):
            sent.append((_sess, bytes(msgs), reliable))
            return True

        def capture_later(delay, callback):
            scheduled.append((delay, callback))
            return None

        with mock.patch.object(emulator, "build_remote_vehicle_messages", return_value=b"INTRO"), \
                mock.patch.object(emulator, "send_avatar_messages", side_effect=capture_send), \
                mock.patch.object(emulator, "runtime_call_later", side_effect=capture_later):
            emulator.broadcast_remote_vehicle_shot(
                object(), source, 123.0, (0.0, 2.0, 40.0),
                (0.0, 0.0, -100.0), 9.81, 0)

        self.assertEqual(len(sent), 1)
        self.assertIs(sent[0][0], observer)
        self.assertFalse(sent[0][1].startswith(b"INTRO"))
        self.assertEqual(
            _message_ids(sent[0][1]),
            [emulator.VEHICLE_SHOW_SHOOTING_MSG_ID,
             emulator.AVATAR_SHOW_TRACER_MSG_ID])
        self.assertEqual(scheduled, [])

    def test_unknown_remote_shot_retries_shooting_after_intro(self):
        source = _make_combat_session(175, 1, (0.0, 0.0, 40.0))
        observer = _make_combat_session(176, 2, (0.0, 0.0, 20.0))
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[observer["account_id"]] = observer
        sent = []
        scheduled = []

        def capture_send(_sock, _addr, _sess, msgs, _label, reliable=True):
            sent.append((_sess, bytes(msgs), reliable))
            return True

        def capture_later(delay, callback):
            scheduled.append((delay, callback))
            return None

        with mock.patch.object(emulator, "build_remote_vehicle_messages", return_value=b"INTRO"), \
                mock.patch.object(emulator, "send_avatar_messages", side_effect=capture_send), \
                mock.patch.object(emulator, "runtime_call_later", side_effect=capture_later):
            emulator.broadcast_remote_vehicle_shot(
                object(), source, 123.0, (0.0, 2.0, 40.0),
                (0.0, 0.0, -100.0), 9.81, 0)

        self.assertEqual(len(sent), 1)
        self.assertIs(sent[0][0], observer)
        self.assertTrue(sent[0][1].startswith(b"INTRO"))
        self.assertEqual(
            _message_ids(sent[0][1][len(b"INTRO"):]),
            [emulator.VEHICLE_SHOW_SHOOTING_MSG_ID,
             emulator.AVATAR_SHOW_TRACER_MSG_ID])
        self.assertIn(source["account_id"], observer["known_remote_accounts"])
        self.assertEqual(len(scheduled), 1)
        self.assertAlmostEqual(scheduled[0][0], emulator.REMOTE_SHOT_SOUND_DELAY)

        before_callback = len(sent)
        with mock.patch.object(emulator, "send_avatar_messages", side_effect=capture_send):
            scheduled[0][1]()
        self.assertEqual(len(sent), before_callback + 1)
        self.assertEqual(_message_ids(sent[-1][1]),
                         [emulator.VEHICLE_SHOW_SHOOTING_MSG_ID])

    def test_direct_shot_impact_is_scheduled_with_stop_and_explode(self):
        shell = _make_shell(compact=9104, penetration=200.0, damage=100.0,
                            speed=500.0, gravity=9.81)
        tank = _make_combat_vehicle()
        tank.update({
            "defaultAmmo": [9104, 1],
            "shells": [shell],
            "reloadTime": 5.0,
        })
        source = _make_combat_session(177, 1, (0.0, 0.0, 40.0),
                                      vehicle=tank)
        target = _make_combat_session(178, 2, (0.0, 0.0, 20.0), health=300)
        source["battle_current_shell"] = 9104
        source["battle_ammo_stock"] = {9104: 1}
        source["battle_target_pos"] = (0.0, 1.3, 20.0)
        source["battle_target_pos_time"] = time.time()
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[target["account_id"]] = target
        sent = []
        scheduled = []

        def capture_send(_sock, _addr, _sess, msgs, _label, reliable=True):
            sent.append(bytes(msgs))
            return True

        def capture_later(delay, callback):
            scheduled.append((delay, callback))
            return None

        with mock.patch.object(emulator, "ray_static_obstacle_hit", return_value=None), \
                mock.patch.object(emulator, "broadcast_remote_vehicle_shot", return_value=None), \
                mock.patch.object(emulator, "send_remote_vehicle", return_value=None), \
                mock.patch.object(emulator, "send_avatar_messages", side_effect=capture_send), \
                mock.patch.object(emulator, "runtime_call_later", side_effect=capture_later):
            emulator.handle_vehicle_shot(object(), source["addr"], source)

        immediate_ids = []
        for messages in sent:
            immediate_ids.extend(_message_ids(messages))
        self.assertIn(emulator.AVATAR_SHOW_TRACER_MSG_ID, immediate_ids)
        self.assertNotIn(emulator.AVATAR_STOP_TRACER_MSG_ID, immediate_ids)
        self.assertNotIn(emulator.AVATAR_EXPLODE_PROJECTILE_MSG_ID, immediate_ids)
        impact = min(scheduled, key=lambda item: item[0])
        self.assertGreaterEqual(impact[0], emulator.DIRECT_PROJECTILE_IMPACT_MIN_DELAY)
        self.assertLessEqual(impact[0], emulator.DIRECT_PROJECTILE_IMPACT_MAX_DELAY)

        before_callback = len(sent)
        with mock.patch.object(emulator, "send_remote_vehicle", return_value=None), \
                mock.patch.object(emulator, "send_avatar_messages", side_effect=capture_send):
            impact[1]()
        delayed_ids = []
        for messages in sent[before_callback:]:
            delayed_ids.extend(_message_ids(messages))
        self.assertIn(emulator.AVATAR_STOP_TRACER_MSG_ID, delayed_ids)
        self.assertIn(emulator.AVATAR_EXPLODE_PROJECTILE_MSG_ID, delayed_ids)

    def test_ap_penetration_reduces_health_and_updates_stats(self):
        source = _make_combat_session(101, 1, (0.0, 0.0, 40.0))
        target = _make_combat_session(102, 2, (0.0, 0.0, 20.0), health=300)
        source["battle_last_shot_target_pos"] = (0.0, 1.3, 20.0)
        source["battle_last_shot_target_pos_time"] = time.time()
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[target["account_id"]] = target
        shell = _make_shell(penetration=200.0, damage=100.0)
        sent_messages = []

        def capture_send(_sock, _addr, _sess, msgs, _label, reliable=True):
            sent_messages.append(bytes(msgs))
            return True

        with mock.patch.object(emulator, "ray_static_obstacle_hit", return_value=None), \
                mock.patch.object(emulator, "send_avatar_messages", side_effect=capture_send), \
                mock.patch.object(emulator, "send_remote_vehicle", return_value=None):
            hit_target, damage, resolved = emulator.apply_shot_damage(
                object(), source, shell, (0.0, 1.0, 40.0), (0.0, 0.0, -1.0))

        self.assertIs(hit_target, target)
        self.assertEqual(damage, 100)
        self.assertEqual(target["battle_vehicle_health"], 200)
        self.assertEqual(source["battle_damage_dealt"], 100)
        self.assertEqual(target["battle_damage_received"], 100)
        self.assertEqual(source["battle_hits"], 1)
        self.assertEqual(target["battle_shots_received"], 1)
        self.assertIn(102, source["battle_damaged_vehicle_ids"])
        self.assertEqual(resolved["result"], emulator.SHOT_RESULT_ARMOR_PIERCED)
        self.assertTrue(sent_messages)
        self.assertIn(bytes([emulator.VEHICLE_SHOW_DAMAGE_FROM_EXPLOSION_MSG_ID]),
                      [msg[:1] for msg in sent_messages])

    def test_direct_marker_hit_prevents_false_side_ricochet(self):
        source = _make_combat_session(
            181, 1, (-250.99546460964893, 34.04087404533013, -230.93543279776856))
        target_vehicle = _make_combat_vehicle(
            health=650, hull=(75.0, 50.0, 30.0), turret=(20.0, 0.0, 0.0))
        target_vehicle["name"] = "Object_261"
        target = _make_combat_session(
            182, 2, (263.13701608027003, 36.74779019041238, 254.17239562026495),
            health=650, vehicle=target_vehicle)
        target["battle_yaw"] = math.radians(30.0)
        marker = (262.0161133, 38.4567375, 254.5153198)
        source["battle_last_shot_target_pos"] = marker
        source["battle_last_shot_target_pos_time"] = time.time()
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[target["account_id"]] = target
        shell = _make_shell(compact=11290, penetration=241.0, damage=490.0,
                            speed=920.0)
        shot_pos = (-250.99546460964893, 36.04087404533013, -230.93543279776856)
        shot_vec = emulator.normalize_vec((
            marker[0] - shot_pos[0],
            marker[1] - shot_pos[1],
            marker[2] - shot_pos[2],
        ))

        with mock.patch.object(emulator, "ray_static_obstacle_hit", return_value=None), \
                mock.patch.object(emulator, "send_avatar_messages", return_value=True), \
                mock.patch.object(emulator, "send_remote_vehicle", return_value=None):
            hit_target, damage, resolved = emulator.apply_shot_damage(
                object(), source, shell, shot_pos, shot_vec)

        self.assertIs(hit_target, target)
        self.assertEqual(damage, 490)
        self.assertEqual(target["battle_vehicle_health"], 160)
        self.assertEqual(resolved["result"], emulator.SHOT_RESULT_ARMOR_PIERCED)
        self.assertGreater(resolved["impactCos"], emulator.SHOT_ARMOR_AUTORICOCHET_COS)

    def test_direct_marker_fix_ignores_off_silhouette_marker(self):
        source = _make_combat_session(183, 1, (3.2, 0.0, -50.0))
        target = _make_combat_session(184, 2, (0.0, 0.0, 0.0), health=300)
        marker = (3.2, 1.3, 0.0)
        source["battle_last_shot_target_pos"] = marker
        source["battle_last_shot_target_pos_time"] = time.time()
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[target["account_id"]] = target

        with mock.patch.object(emulator, "ray_static_obstacle_hit", return_value=None), \
                mock.patch.object(emulator, "send_avatar_messages", return_value=True), \
                mock.patch.object(emulator, "send_remote_vehicle", return_value=None):
            hit_target, damage, resolved = emulator.apply_shot_damage(
                object(), source, _make_shell(penetration=500.0, damage=100.0),
                (3.2, 1.3, -50.0), (0.0, 0.0, 1.0))

        self.assertIs(hit_target, target)
        self.assertEqual(damage, 0)
        self.assertEqual(target["battle_vehicle_health"], 300)
        self.assertEqual(resolved["result"], emulator.SHOT_RESULT_RICOCHET)

    def test_ap_ricochet_does_not_reduce_health(self):
        source = _make_combat_session(111, 1, (0.0, 0.0, 0.0))
        target = _make_combat_session(112, 2, (0.0, 0.0, 20.0), health=300)
        hit_info = {
            "target": target,
            "distance": 100.0,
            "armor": 50.0,
            "impactCos": 0.2,
            "component": "hull",
            "zone": "front",
            "hitLocal": (0.0, 1.0, 5.2),
            "dimensions": emulator.armor_dimensions({}),
            "localShotDir": (0.0, 0.0, -1.0),
        }

        with mock.patch.object(emulator, "find_shot_target", return_value=hit_info), \
                mock.patch.object(emulator, "send_avatar_messages", return_value=True), \
                mock.patch.object(emulator, "send_remote_vehicle", return_value=None):
            _target, damage, resolved = emulator.apply_shot_damage(
                object(), source, _make_shell(), (0.0, 0.0, 0.0), (0.0, 0.0, -1.0))

        self.assertEqual(damage, 0)
        self.assertEqual(target["battle_vehicle_health"], 300)
        self.assertEqual(resolved["result"], emulator.SHOT_RESULT_RICOCHET)
        self.assertEqual(source["battle_hits"], 1)
        self.assertEqual(target["battle_shots_received"], 1)

    def test_ap_non_penetration_does_not_reduce_health(self):
        source = _make_combat_session(121, 1, (0.0, 0.0, 0.0))
        target = _make_combat_session(122, 2, (0.0, 0.0, 20.0), health=300)
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[target["account_id"]] = target
        hit_info = {
            "target": target,
            "distance": 100.0,
            "armor": 300.0,
            "impactCos": 1.0,
            "component": "hull",
            "zone": "front",
            "hitLocal": (0.0, 1.0, 5.2),
            "dimensions": emulator.armor_dimensions({}),
            "localShotDir": (0.0, 0.0, -1.0),
        }
        sent_messages = []

        def capture_send(_sock, _addr, _sess, msgs, _label, reliable=True):
            sent_messages.append(bytes(msgs))
            return True

        with mock.patch.object(emulator, "find_shot_target", return_value=hit_info), \
                mock.patch.object(emulator, "send_avatar_messages", side_effect=capture_send), \
                mock.patch.object(emulator, "send_remote_vehicle", return_value=None):
            _target, damage, resolved = emulator.apply_shot_damage(
                object(), source, _make_shell(penetration=100.0),
                (0.0, 0.0, 0.0), (0.0, 0.0, -1.0))

        self.assertEqual(damage, 0)
        self.assertEqual(target["battle_vehicle_health"], 300)
        self.assertEqual(resolved["result"], emulator.SHOT_RESULT_ARMOR_NOT_PIERCED)
        self.assertTrue(sent_messages)
        self.assertIn(bytes([emulator.VEHICLE_SHOW_DAMAGE_FROM_EXPLOSION_MSG_ID]),
                      [msg[:1] for msg in sent_messages])

    def test_he_non_penetration_deals_reduced_damage(self):
        source = _make_combat_session(131, 1, (0.0, 0.0, 0.0))
        target = _make_combat_session(132, 2, (0.0, 0.0, 20.0), health=300)
        hit_info = {
            "target": target,
            "distance": 100.0,
            "armor": 300.0,
            "impactCos": 1.0,
            "component": "hull",
            "zone": "front",
            "hitLocal": (0.0, 1.0, 5.2),
            "dimensions": emulator.armor_dimensions({}),
            "localShotDir": (0.0, 0.0, -1.0),
        }

        with mock.patch.object(emulator, "find_shot_target", return_value=hit_info), \
                mock.patch.object(emulator, "send_avatar_messages", return_value=True), \
                mock.patch.object(emulator, "send_remote_vehicle", return_value=None):
            _target, damage, resolved = emulator.apply_shot_damage(
                object(), source,
                _make_shell(kind="HIGH_EXPLOSIVE", penetration=10.0, damage=100.0),
                (0.0, 0.0, 0.0), (0.0, 0.0, -1.0))

        self.assertEqual(damage, 18)
        self.assertEqual(target["battle_vehicle_health"], 282)
        self.assertEqual(resolved["result"], emulator.SHOT_RESULT_ARMOR_NOT_PIERCED)

    def test_dead_target_and_stale_marker_are_safe_misses(self):
        source = _make_combat_session(141, 1, (0.0, 0.0, 40.0))
        target = _make_combat_session(142, 2, (0.0, 0.0, 20.0), health=0)
        source["battle_last_shot_target_pos"] = (0.0, 1.3, 20.0)
        source["battle_last_shot_target_pos_time"] = time.time()
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[target["account_id"]] = target

        with mock.patch.object(emulator, "ray_static_obstacle_hit", return_value=None):
            hit_target, damage, resolved = emulator.apply_shot_damage(
                object(), source, _make_shell(), (0.0, 1.0, 40.0), (0.0, 0.0, -1.0))

        self.assertIsNone(hit_target)
        self.assertEqual(damage, 0)
        self.assertIsNone(resolved)
        self.assertEqual(source["battle_hits"], 0)
        source["battle_last_shot_target_pos_time"] = time.time() - 10.0

        hit_target, damage, resolved = emulator.apply_shot_damage(
            object(), source, _make_shell(), (0.0, 1.0, 40.0), (0.0, 0.0, -1.0))

        self.assertIsNone(hit_target)
        self.assertEqual(damage, 0)
        self.assertIsNone(resolved)
        self.assertEqual(source["battle_hits"], 0)

    def test_kill_updates_frags_and_killed_ids(self):
        source = _make_combat_session(151, 1, (0.0, 0.0, 0.0))
        target = _make_combat_session(152, 2, (0.0, 0.0, 20.0), health=80)
        hit_info = {
            "target": target,
            "distance": 100.0,
            "armor": 50.0,
            "impactCos": 1.0,
            "component": "hull",
            "zone": "front",
            "hitLocal": (0.0, 1.0, 5.2),
            "dimensions": emulator.armor_dimensions({}),
            "localShotDir": (0.0, 0.0, -1.0),
        }
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[target["account_id"]] = target

        with mock.patch.object(emulator, "find_shot_target", return_value=hit_info), \
                mock.patch.object(emulator, "send_avatar_messages", return_value=True), \
                mock.patch.object(emulator, "send_remote_vehicle", return_value=None), \
                mock.patch.object(emulator, "runtime_call_later", return_value=None), \
                mock.patch.object(emulator, "mark_battle_finished", return_value=None):
            _target, damage, _resolved = emulator.apply_shot_damage(
                object(), source, _make_shell(penetration=200.0, damage=100.0),
                (0.0, 0.0, 0.0), (0.0, 0.0, -1.0))

        self.assertEqual(damage, 80)
        self.assertEqual(target["battle_vehicle_health"], 0)
        self.assertEqual(source["battle_frags"], 1)
        self.assertIn(152, source["battle_killed_vehicle_ids"])
        self.assertEqual(target["battle_killer_account_id"], 151)

    def test_pack_damage_segment_clamps_to_uint64(self):
        for component in ("hull", "turret", "unknown"):
            value = emulator.pack_damage_segment({
                "component": component,
                "result": 999,
                "hitLocal": (float("nan"), 9999.0, -9999.0),
                "dimensions": {
                    "halfWidth": -10.0,
                    "halfLength": 0.0,
                    "minHeight": 5.0,
                    "hullTop": 5.0,
                    "maxHeight": 5.0,
                },
                "localShotDir": (float("inf"), 0.0, 0.0),
            })

            self.assertIsInstance(value, int)
            self.assertGreaterEqual(value, 0)
            self.assertLessEqual(value, 0xffffffffffffffff)

    def test_client_damage_effects_always_uses_explosion_message(self):
        source = _make_combat_session(161, 1, (0.0, 0.0, 0.0))
        target = _make_combat_session(162, 2, (0.0, 0.0, 20.0), health=300)
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[target["account_id"]] = target
        hit_info = {
            "target": target,
            "distance": 100.0,
            "armor": 50.0,
            "impactCos": 1.0,
            "component": "hull",
            "zone": "front",
            "hitWorld": (0.0, 1.0, 20.0),
            "hitLocal": (0.0, 1.0, 5.2),
            "dimensions": emulator.armor_dimensions({}),
            "localShotDir": (0.0, 0.0, -1.0),
            "result": emulator.SHOT_RESULT_ARMOR_PIERCED,
        }
        sent_messages = []

        def capture_send(_sock, _addr, _sess, msgs, _label, reliable=True):
            sent_messages.append(bytes(msgs))
            return True

        emulator.ENABLE_CLIENT_SHOT_DAMAGE_EFFECTS = True
        with mock.patch.object(emulator, "send_avatar_messages", side_effect=capture_send), \
                mock.patch.object(emulator, "send_remote_vehicle", return_value=None):
            emulator.broadcast_vehicle_shot_feedback(
                object(), target, source, hit_info, 0, 100)

        self.assertTrue(sent_messages)
        msg_ids = [msg[:1] for msg in sent_messages]
        self.assertIn(bytes([emulator.VEHICLE_SHOW_DAMAGE_FROM_EXPLOSION_MSG_ID]),
                      msg_ids)
        self.assertNotIn(bytes([emulator.VEHICLE_SHOW_DAMAGE_FROM_SHOT_MSG_ID]),
                         msg_ids)

    def test_artillery_splash_uses_damage_from_explosion_message(self):
        source = _make_combat_session(181, 1, (0.0, 0.0, 0.0))
        target = _make_combat_session(182, 2, (0.0, 0.0, 20.0), health=300)
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[target["account_id"]] = target
        hit_info = {
            "target": target,
            "distance": 100.0,
            "armor": 0.0,
            "impactCos": 1.0,
            "component": "hull",
            "zone": "splash",
            "hitWorld": (1.5, 0.5, 22.0),
            "hitLocal": (1.5, 0.5, 2.0),
            "dimensions": emulator.armor_dimensions({}),
            "localShotDir": (0.0, -1.0, 0.0),
            "result": emulator.SHOT_RESULT_CRITICAL_HIT,
            "artillerySplash": True,
            "artilleryDirectHit": False,
        }
        sent_messages = []

        def capture_send(_sock, _addr, _sess, msgs, _label, reliable=True):
            sent_messages.append(bytes(msgs))
            return True

        emulator.ENABLE_CLIENT_SHOT_DAMAGE_EFFECTS = True
        with mock.patch.object(emulator, "send_avatar_messages", side_effect=capture_send), \
                mock.patch.object(emulator, "send_remote_vehicle", return_value=None):
            emulator.broadcast_vehicle_shot_feedback(
                object(), target, source, hit_info, 11, 150)

        self.assertTrue(sent_messages)
        msg_ids = [msg[:1] for msg in sent_messages]
        self.assertIn(
            bytes([emulator.VEHICLE_SHOW_DAMAGE_FROM_EXPLOSION_MSG_ID]),
            msg_ids)
        self.assertNotIn(
            bytes([emulator.VEHICLE_SHOW_DAMAGE_FROM_SHOT_MSG_ID]),
            msg_ids)

    def test_ap_direct_hit_uses_damage_from_explosion_message(self):
        source = _make_combat_session(183, 1, (0.0, 0.0, 0.0))
        target = _make_combat_session(184, 2, (0.0, 0.0, 20.0), health=300)
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[target["account_id"]] = target
        hit_info = {
            "target": target,
            "distance": 100.0,
            "armor": 50.0,
            "impactCos": 1.0,
            "component": "hull",
            "zone": "front",
            "hitWorld": (0.0, 1.0, 20.0),
            "hitLocal": (0.0, 1.0, 5.2),
            "dimensions": emulator.armor_dimensions({}),
            "localShotDir": (0.0, 0.0, -1.0),
            "result": emulator.SHOT_RESULT_ARMOR_PIERCED,
            "artillerySplash": False,
            "artilleryDirectHit": False,
        }
        shell = _make_shell(kind="ARMOR_PIERCING", penetration=200.0)
        sent_messages = []

        def capture_send(_sock, _addr, _sess, msgs, _label, reliable=True):
            sent_messages.append(bytes(msgs))
            return True

        emulator.ENABLE_CLIENT_SHOT_DAMAGE_EFFECTS = True
        with mock.patch.object(emulator, "send_avatar_messages", side_effect=capture_send), \
                mock.patch.object(emulator, "send_remote_vehicle", return_value=None):
            emulator.broadcast_vehicle_shot_feedback(
                object(), target, source, hit_info, 6, 120, shell)

        self.assertTrue(sent_messages)
        msg_ids = [msg[:1] for msg in sent_messages]
        self.assertIn(
            bytes([emulator.VEHICLE_SHOW_DAMAGE_FROM_EXPLOSION_MSG_ID]),
            msg_ids)
        self.assertNotIn(
            bytes([emulator.VEHICLE_SHOW_DAMAGE_FROM_SHOT_MSG_ID]),
            msg_ids)

    def test_he_direct_hit_uses_damage_from_explosion_message(self):
        source = _make_combat_session(185, 1, (0.0, 0.0, 0.0))
        target = _make_combat_session(186, 2, (0.0, 0.0, 20.0), health=300)
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[target["account_id"]] = target
        hit_info = {
            "target": target,
            "distance": 100.0,
            "armor": 50.0,
            "impactCos": 1.0,
            "component": "hull",
            "zone": "front",
            "hitWorld": (0.0, 1.0, 20.0),
            "hitLocal": (0.0, 1.0, 5.2),
            "dimensions": emulator.armor_dimensions({}),
            "localShotDir": (0.0, 0.0, -1.0),
            "result": emulator.SHOT_RESULT_CRITICAL_HIT,
            "artillerySplash": False,
            "artilleryDirectHit": True,
        }
        shell = _make_shell(kind="HIGH_EXPLOSIVE", penetration=80.0,
                            explosion_radius=5.0)
        sent_messages = []

        def capture_send(_sock, _addr, _sess, msgs, _label, reliable=True):
            sent_messages.append(bytes(msgs))
            return True

        emulator.ENABLE_CLIENT_SHOT_DAMAGE_EFFECTS = True
        with mock.patch.object(emulator, "send_avatar_messages", side_effect=capture_send), \
                mock.patch.object(emulator, "send_remote_vehicle", return_value=None):
            emulator.broadcast_vehicle_shot_feedback(
                object(), target, source, hit_info, 11, 1156, shell)

        self.assertTrue(sent_messages)
        msg_ids = [msg[:1] for msg in sent_messages]
        self.assertIn(
            bytes([emulator.VEHICLE_SHOW_DAMAGE_FROM_EXPLOSION_MSG_ID]),
            msg_ids)
        self.assertNotIn(
            bytes([emulator.VEHICLE_SHOW_DAMAGE_FROM_SHOT_MSG_ID]),
            msg_ids)


if __name__ == "__main__":
    unittest.main()
