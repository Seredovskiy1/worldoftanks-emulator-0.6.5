import struct
import threading
import unittest

import emulator
from server_core import EventLoopRuntime


class _FakeBattleModule:
    BATTLE_MOTION_TICK = 0.05
    ARENA_TYPE_KARELIA = emulator.ARENA_TYPE_KARELIA
    ARENA_SPAWN_POS = emulator.ARENA_SPAWN_POS

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

    def test_scheduler_order_and_cancel(self):
        runtime = EventLoopRuntime(emulator)
        seen = []
        runtime.call_later(0.0, lambda: seen.append("b"))
        cancelled = runtime.call_later(0.0, lambda: seen.append("x"))
        runtime.call_later(0.0, lambda: seen.append("c"))
        cancelled.cancel()
        runtime._run_due()
        self.assertEqual(seen, ["b", "c"])

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


if __name__ == "__main__":
    unittest.main()
