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


def _make_shell(kind="ARMOR_PIERCING", penetration=200.0, damage=100.0):
    return {
        "kind": kind,
        "piercingPower": [penetration, penetration],
        "piercingPowerRandomization": 0.0,
        "damage": [damage, damage],
        "damageRandomization": 0.0,
        "normalizationAngle": 0.0,
        "ricochetAngleCos": emulator.SHOT_ARMOR_AUTORICOCHET_COS,
        "effectsIndex": 0,
    }


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


class RuntimeCoreTests(unittest.TestCase):
    def setUp(self):
        self._active_battle_accounts = dict(emulator.active_battle_accounts)
        self._enable_client_shot_damage_effects = emulator.ENABLE_CLIENT_SHOT_DAMAGE_EFFECTS
        emulator.active_battle_accounts.clear()
        emulator.ENABLE_CLIENT_SHOT_DAMAGE_EFFECTS = False

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
        self.assertNotIn(bytes([emulator.VEHICLE_SHOW_DAMAGE_FROM_SHOT_MSG_ID]),
                         [msg[:1] for msg in sent_messages])

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
                mock.patch.object(emulator, "pack_damage_segment",
                                  side_effect=AssertionError("damage segment should stay disabled")), \
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
                mock.patch.object(emulator, "pack_damage_segment",
                                  side_effect=AssertionError("damage segment should stay disabled")), \
                mock.patch.object(emulator, "send_avatar_messages", side_effect=capture_send), \
                mock.patch.object(emulator, "send_remote_vehicle", return_value=None):
            _target, damage, resolved = emulator.apply_shot_damage(
                object(), source, _make_shell(penetration=100.0),
                (0.0, 0.0, 0.0), (0.0, 0.0, -1.0))

        self.assertEqual(damage, 0)
        self.assertEqual(target["battle_vehicle_health"], 300)
        self.assertEqual(resolved["result"], emulator.SHOT_RESULT_ARMOR_NOT_PIERCED)
        self.assertEqual(sent_messages, [])

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

    def test_client_damage_effects_debug_path_can_pack_segment(self):
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
        self.assertIn(bytes([emulator.VEHICLE_SHOW_DAMAGE_FROM_SHOT_MSG_ID]),
                      [msg[:1] for msg in sent_messages])


if __name__ == "__main__":
    unittest.main()
