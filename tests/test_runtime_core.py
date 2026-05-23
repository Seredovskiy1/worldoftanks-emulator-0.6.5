import io
import math
import pickle
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
        self.minimap_updates = []
        self.enable_minimap_updates = False

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
        account_id = sess.get("account_id")
        if account_id:
            observer.setdefault("known_remote_accounts", set()).add(account_id)
            observer.setdefault("arena_remote_accounts", set()).add(account_id)

    def is_vehicle_visible_to(self, observer, sess):
        hidden_for = sess.get("force_hidden_for", set())
        if observer.get("account_id") in hidden_for:
            return False
        return not sess.get("force_hidden", False)

    def hide_remote_vehicle(self, sock, observer, sess):
        account_id = sess.get("account_id")
        known = observer.setdefault("known_remote_accounts", set())
        if account_id not in known:
            return False
        addr = observer.get("addr")
        if addr:
            self.send_avatar_messages(sock, addr, observer, b"LEAVE",
                                      "", reliable=True)
        known.discard(account_id)
        return False

    def update_remote_vehicle_visibility(self, sock, observer, sess):
        if not self.is_vehicle_visible_to(observer, sess):
            self.hide_remote_vehicle(sock, observer, sess)
            return False
        account_id = sess.get("account_id")
        if account_id not in observer.setdefault("known_remote_accounts", set()):
            self.send_remote_vehicle(sock, observer, sess)
        return True

    def build_minimap_positions_update(self, observer, sessions):
        if not self.enable_minimap_updates:
            return b""
        visible = []
        for sess in sessions:
            if sess.get("battle_match_id") != observer.get("battle_match_id"):
                continue
            if sess is observer:
                visible.append(sess.get("account_id"))
            elif (sess.get("account_id") in observer.setdefault("known_remote_accounts", set()) and
                    self.is_vehicle_visible_to(observer, sess)):
                visible.append(sess.get("account_id"))
        self.minimap_updates.append((observer, tuple(visible)))
        return b"MINIMAP"

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
                         turret=(60.0, 45.0, 40.0),
                         weight_kg=30000.0):
    return {
        "name": "TestTank",
        "maxHealth": health,
        "totalWeightKg": weight_kg,
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


def _set_gun_direction(sess, shot_vec):
    shot_vec = emulator.normalize_vec(shot_vec)
    horizontal = math.sqrt(shot_vec[0] * shot_vec[0] +
                           shot_vec[2] * shot_vec[2])
    sess["battle_turret_yaw"] = math.atan2(shot_vec[0], shot_vec[2])
    sess["battle_gun_pitch"] = math.atan2(shot_vec[1], max(0.001, horizontal))
    return shot_vec


def _aim_gun_at(sess, target_pos, shell, high_arc=False):
    pos = sess.get("battle_pos", (0.0, 0.0, 0.0))
    shot_pos = (pos[0], pos[1] + 2.0, pos[2])
    shot_vec = emulator.ballistic_shot_vec(
        shot_pos, target_pos, float(shell.get("speed", 800.0)),
        float(shell.get("gravity", 9.81)), high_arc=high_arc)
    return _set_gun_direction(sess, shot_vec)


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


def _bw_read_int(data, pos):
    value = data[pos]
    pos += 1
    if value == 0xFF:
        value = data[pos] | (data[pos + 1] << 8) | (data[pos + 2] << 16)
        pos += 3
    return value, pos


def _arena_update_values(messages, update_type):
    for payload in _message_payloads(messages, emulator.AVATAR_UPDATEARENA_MSG_ID):
        if payload[4] != update_type:
            continue
        size, pos = _bw_read_int(payload, 5)
        return pickle.loads(payload[pos:pos + size])
    return None


def _detailed_position_entity_ids(messages):
    out = []
    size = len(emulator.build_vehicle_motion_update_for(
        1, (0.0, 0.0, 0.0), 0.0))
    pos = 0
    while pos + size <= len(messages):
        if messages[pos] != emulator.CLIENT_DETAILED_POSITION_MSG_ID:
            break
        out.append(struct.unpack_from("<I", messages, pos + 1)[0])
        pos += size
    return out


def _parse_update_positions(messages):
    payloads = _message_payloads(messages, emulator.AVATAR_UPDATE_POSITIONS_MSG_ID)
    if not payloads:
        return [], []
    payload = payloads[0]
    pos = 4
    index_count = struct.unpack_from("<i", payload, pos)[0]
    pos += 4
    indices = list(payload[pos:pos + index_count])
    pos += index_count
    position_count = struct.unpack_from("<i", payload, pos)[0]
    pos += 4
    positions = [
        struct.unpack_from("<h", payload, pos + i * 2)[0]
        for i in range(position_count)
    ]
    return indices, positions


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
        self._keep_remote_entities_on_visibility_loss = emulator.KEEP_REMOTE_ENTITIES_ON_VISIBILITY_LOSS
        self._shot_dispersion_enabled = emulator.SHOT_DISPERSION_ENABLED
        self._shot_dispersion_server_radius_scale = emulator.SHOT_DISPERSION_SERVER_RADIUS_SCALE
        self._shot_dispersion_center_bias = emulator.SHOT_DISPERSION_CENTER_BIAS
        self._shot_damage_randomization = emulator.SHOT_DAMAGE_RANDOMIZATION
        emulator.active_battle_accounts.clear()
        emulator.ENABLE_CLIENT_SHOT_DAMAGE_EFFECTS = False
        emulator.KEEP_REMOTE_ENTITIES_ON_VISIBILITY_LOSS = False
        emulator.SHOT_DISPERSION_ENABLED = False
        emulator.SHOT_DISPERSION_SERVER_RADIUS_SCALE = 0.45
        emulator.SHOT_DISPERSION_CENTER_BIAS = 2.5
        emulator.SHOT_DAMAGE_RANDOMIZATION = 0.0

    def tearDown(self):
        emulator.active_battle_accounts.clear()
        emulator.active_battle_accounts.update(self._active_battle_accounts)
        emulator.ENABLE_CLIENT_SHOT_DAMAGE_EFFECTS = self._enable_client_shot_damage_effects
        emulator.KEEP_REMOTE_ENTITIES_ON_VISIBILITY_LOSS = self._keep_remote_entities_on_visibility_loss
        emulator.SHOT_DISPERSION_ENABLED = self._shot_dispersion_enabled
        emulator.SHOT_DISPERSION_SERVER_RADIUS_SCALE = self._shot_dispersion_server_radius_scale
        emulator.SHOT_DISPERSION_CENTER_BIAS = self._shot_dispersion_center_bias
        emulator.SHOT_DAMAGE_RANDOMIZATION = self._shot_damage_randomization

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

    def test_schedule_init_bundle_runs_once_after_baseapp_login(self):
        sess = {"init_sent": False, "init_scheduled": False}
        scheduled = []

        def capture_later(delay, callback):
            scheduled.append((delay, callback))
            return object()

        with mock.patch.object(emulator, "runtime_call_later",
                               side_effect=capture_later):
            emulator.schedule_init_bundle(object(), ("127.0.0.1", 20017), sess)
            emulator.schedule_init_bundle(object(), ("127.0.0.1", 20017), sess)

        self.assertEqual(len(scheduled), 1)
        self.assertEqual(scheduled[0][0], emulator.BASEAPP_INIT_DELAY_SECONDS)
        self.assertTrue(sess["init_scheduled"])

        with mock.patch.object(emulator, "send_init_bundle") as send_init:
            scheduled[0][1]()

        send_init.assert_called_once()
        self.assertFalse(sess["init_scheduled"])

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

    def test_loaded_vehicle_levels_include_tier_eight_spgs(self):
        vehicles = {v["name"]: v for v in emulator.load_all_vehicles()}
        for name in ("Object_261", "G_E", "T92"):
            self.assertEqual(vehicles[name]["level"], 8)
            self.assertTrue(emulator.is_artillery_vehicle(vehicles[name]))

    def test_light_tank_spots_farther_than_heavy_by_fallback_view_range(self):
        light = _make_combat_session(
            41, 1, (0.0, 0.0, 0.0),
            vehicle={"vehicleClass": "lightTank"})
        heavy = _make_combat_session(
            42, 1, (0.0, 0.0, 0.0),
            vehicle={"vehicleClass": "heavyTank"})
        target = _make_combat_session(
            43, 2, (0.0, 0.0, 320.0),
            vehicle={"vehicleClass": "heavyTank"})

        with mock.patch.object(emulator, "iter_spotting_bushes_near_segment",
                               side_effect=lambda *args, **kwargs: iter(())):
            self.assertTrue(emulator.is_vehicle_visible_to(light, target))
            self.assertFalse(emulator.is_vehicle_visible_to(heavy, target))

    def test_light_effective_view_range_beats_heavy_even_when_xml_is_lower(self):
        vehicles = {v["name"]: v for v in emulator.load_all_vehicles()}
        a20 = vehicles["A-20"]
        maus = vehicles["Maus"]

        self.assertGreater(maus["circularVisionRadius"],
                           a20["circularVisionRadius"])
        self.assertGreater(emulator.vehicle_view_range(a20),
                           emulator.vehicle_view_range(maus))

    def test_light_camo_is_better_than_heavy_camo_from_xml_data(self):
        vehicles = {v["name"]: v for v in emulator.load_all_vehicles()}
        a20 = vehicles["A-20"]
        maus = vehicles["Maus"]

        self.assertGreater(
            emulator.vehicle_base_invisibility(a20, moving=True),
            emulator.vehicle_base_invisibility(maus, moving=True))
        self.assertGreater(
            emulator.vehicle_base_invisibility(a20, moving=False),
            emulator.vehicle_base_invisibility(maus, moving=False))

    def test_a20_must_close_inside_raw_view_range_to_spot_stationary_maus(self):
        vehicles = {v["name"]: v for v in emulator.load_all_vehicles()}
        a20 = _make_combat_session(
            57, 1, (0.0, 0.0, 0.0),
            vehicle=vehicles["A-20"])
        maus = _make_combat_session(
            58, 2, (0.0, 0.0, 360.0),
            vehicle=vehicles["Maus"])

        with mock.patch.object(emulator, "iter_spotting_bushes_near_segment",
                               side_effect=lambda *args, **kwargs: iter(())):
            self.assertFalse(emulator.is_vehicle_visible_to(a20, maus))
            maus["battle_pos"] = (0.0, 0.0, 335.0)
            self.assertTrue(emulator.is_vehicle_visible_to(a20, maus))

    def test_a20_spots_maus_farther_than_maus_spots_a20_through_bush(self):
        vehicles = {v["name"]: v for v in emulator.load_all_vehicles()}
        a20 = _make_combat_session(
            59, 1, (0.0, 0.0, 0.0),
            vehicle=vehicles["A-20"])
        maus = _make_combat_session(
            60, 2, (0.0, 0.0, 300.0),
            vehicle=vehicles["Maus"])
        bush = ((0.0, 125.0, 8.0, b"speedtree/bush.spt"),)

        with mock.patch.object(emulator, "iter_spotting_bushes_near_segment",
                               side_effect=lambda *args, **kwargs: iter(bush)):
            self.assertTrue(emulator.is_vehicle_visible_to(a20, maus))
            self.assertFalse(emulator.is_vehicle_visible_to(maus, a20))

    def test_a20_keeps_camo_advantage_after_shot_with_bush_cover(self):
        vehicles = {v["name"]: v for v in emulator.load_all_vehicles()}
        a20 = _make_combat_session(
            61, 1, (0.0, 0.0, 0.0),
            vehicle=vehicles["A-20"])
        maus = _make_combat_session(
            62, 2, (0.0, 0.0, 300.0),
            vehicle=vehicles["Maus"])
        bush = ((0.0, 125.0, 8.0, b"speedtree/bush.spt"),)

        with mock.patch.object(emulator, "iter_spotting_bushes_near_segment",
                               side_effect=lambda *args, **kwargs: iter(bush)):
            emulator.mark_vehicle_shot_visibility_penalty(a20)
            self.assertTrue(emulator.is_vehicle_visible_to(a20, maus))
            self.assertFalse(emulator.is_vehicle_visible_to(maus, a20))

    def test_ally_direct_spot_makes_enemy_visible_to_team(self):
        vehicles = {v["name"]: v for v in emulator.load_all_vehicles()}
        spotter = _make_combat_session(
            63, 1, (0.0, 0.0, 0.0),
            vehicle=vehicles["A-20"])
        ally = _make_combat_session(
            64, 1, (-300.0, 0.0, 0.0),
            vehicle=vehicles["Maus"])
        enemy = _make_combat_session(
            65, 2, (0.0, 0.0, 300.0),
            vehicle=vehicles["Maus"])
        emulator.active_battle_accounts[63] = spotter
        emulator.active_battle_accounts[64] = ally
        emulator.active_battle_accounts[65] = enemy

        with mock.patch.object(emulator, "iter_spotting_bushes_near_segment",
                               side_effect=lambda *args, **kwargs: iter(())):
            self.assertTrue(emulator.is_vehicle_directly_visible_to(
                spotter, enemy))
            self.assertFalse(emulator.is_vehicle_directly_visible_to(
                ally, enemy))
            self.assertTrue(emulator.is_vehicle_visible_to(ally, enemy))

        self.assertIn(enemy["account_id"],
                      spotter["battle_spotted_vehicle_ids"])
        self.assertNotIn(enemy["account_id"],
                         ally.get("battle_spotted_vehicle_ids", set()))

    def test_runtime_shared_spotting_introduces_enemy_and_minimap_to_ally(self):
        vehicles = {v["name"]: v for v in emulator.load_all_vehicles()}
        spotter = _make_combat_session(
            66, 1, (0.0, 0.0, 0.0),
            vehicle=vehicles["A-20"])
        ally = _make_combat_session(
            67, 1, (-300.0, 0.0, 0.0),
            vehicle=vehicles["Maus"])
        enemy = _make_combat_session(
            68, 2, (0.0, 0.0, 300.0),
            vehicle=vehicles["Maus"])
        emulator.active_battle_accounts[66] = spotter
        emulator.active_battle_accounts[67] = ally
        emulator.active_battle_accounts[68] = enemy
        runtime = EventLoopRuntime(emulator)
        sent = []

        def capture_send(_sock, _addr, _sess, msgs, _label, reliable=True):
            sent.append((_sess, bytes(msgs), reliable))
            return True

        with mock.patch.object(emulator, "iter_spotting_bushes_near_segment",
                               side_effect=lambda *args, **kwargs: iter(())), \
                mock.patch.object(emulator, "build_remote_vehicle_messages",
                                  return_value=b"INTRO"), \
                mock.patch.object(emulator, "send_avatar_messages",
                                  side_effect=capture_send), \
                mock.patch.object(emulator, "process_base_capture",
                                  return_value=None):
            runtime._run_battle_tick_once(sock=object())

        self.assertIn(enemy["account_id"], ally["known_remote_accounts"])
        minimap_payloads = []
        for sess, msgs, _reliable in sent:
            if sess is ally:
                offset = msgs.rfind(bytes([emulator.AVATAR_UPDATE_POSITIONS_MSG_ID]))
                if offset >= 0 and offset + 3 <= len(msgs):
                    size = struct.unpack_from("<H", msgs, offset + 1)[0]
                    minimap_payloads.append(msgs[offset + 3:offset + 3 + size])
        self.assertTrue(minimap_payloads)
        indices, positions = _parse_update_positions(
            bytes([emulator.AVATAR_UPDATE_POSITIONS_MSG_ID]) +
            struct.pack("<H", len(minimap_payloads[-1])) +
            minimap_payloads[-1])
        enemy_index = emulator.client_arena_vehicle_ids(ally).index(
            emulator.get_remote_vehicle_id(enemy))
        self.assertIn(enemy_index, indices)
        self.assertIn(300, positions)

    def test_heavy_does_not_spot_distant_camouflaged_target(self):
        observer = _make_combat_session(
            44, 1, (0.0, 0.0, 0.0),
            vehicle={"vehicleClass": "heavyTank"})
        target = _make_combat_session(
            45, 2, (0.0, 0.0, 200.0),
            vehicle={
                "vehicleClass": "lightTank",
                "invisibilityStill": 0.8,
                "invisibilityMoving": 0.7,
            })

        with mock.patch.object(emulator, "iter_spotting_bushes_near_segment",
                               side_effect=lambda *args, **kwargs: iter(())):
            self.assertFalse(emulator.is_vehicle_visible_to(observer, target))

    def test_auto_reveal_distance_always_works(self):
        observer = _make_combat_session(
            46, 1, (0.0, 0.0, 0.0),
            vehicle={"vehicleClass": "heavyTank"})
        target = _make_combat_session(
            47, 2, (0.0, 0.0, 49.0),
            vehicle={
                "vehicleClass": "lightTank",
                "invisibilityStill": 0.95,
                "invisibilityMoving": 0.95,
            })

        self.assertTrue(emulator.is_vehicle_visible_to(observer, target))

    def test_moving_target_is_easier_to_spot_than_still_target(self):
        observer = _make_combat_session(
            48, 1, (0.0, 0.0, 0.0),
            vehicle={
                "vehicleClass": "lightTank",
                "circularVisionRadius": 360.0,
            })
        target = _make_combat_session(
            49, 2, (0.0, 0.0, 250.0),
            vehicle={
                "vehicleClass": "mediumTank",
                "invisibilityMoving": 0.1,
                "invisibilityStill": 0.5,
            })

        with mock.patch.object(emulator, "iter_spotting_bushes_near_segment",
                               side_effect=lambda *args, **kwargs: iter(())):
            target["battle_speed"] = 0.0
            self.assertFalse(emulator.is_vehicle_visible_to(observer, target))
            target["battle_speed"] = 8.0
            self.assertTrue(emulator.is_vehicle_visible_to(observer, target))

    def test_bush_between_observer_and_target_reduces_spotting(self):
        observer = _make_combat_session(
            50, 1, (0.0, 0.0, 0.0),
            vehicle={
                "vehicleClass": "lightTank",
                "circularVisionRadius": 360.0,
            })
        target = _make_combat_session(
            51, 2, (0.0, 0.0, 360.0),
            vehicle={
                "vehicleClass": "mediumTank",
                "invisibilityMoving": 0.05,
                "invisibilityStill": 0.05,
            })

        with mock.patch.object(emulator, "iter_spotting_bushes_near_segment",
                               side_effect=lambda *args, **kwargs: iter(())):
            self.assertTrue(emulator.is_vehicle_visible_to(observer, target))
        with mock.patch.object(emulator, "iter_spotting_bushes_near_segment",
                               side_effect=lambda *args, **kwargs: iter((
                                   (0.0, 160.0, 8.0, b"speedtree/bush.spt"),
                               ))):
            self.assertFalse(emulator.is_vehicle_visible_to(observer, target))

    def test_allies_are_always_visible(self):
        observer = _make_combat_session(
            52, 1, (0.0, 0.0, 0.0),
            vehicle={"vehicleClass": "heavyTank"})
        target = _make_combat_session(
            53, 1, (0.0, 0.0, 1200.0),
            vehicle={
                "vehicleClass": "lightTank",
                "invisibilityMoving": 0.95,
                "invisibilityStill": 0.95,
            })

        self.assertTrue(emulator.is_vehicle_visible_to(observer, target))

    def test_enemy_is_hidden_before_battle_period(self):
        observer = _make_combat_session(
            57, 1, (0.0, 0.0, 0.0),
            vehicle={"vehicleClass": "heavyTank"})
        target = _make_combat_session(
            58, 2, (0.0, 0.0, 100.0),
            vehicle={"vehicleClass": "lightTank"})
        observer["battle_period_active"] = False
        target["battle_period_active"] = False
        sent = []

        def capture_send(_sock, _addr, _sess, msgs, _label, reliable=True):
            sent.append(bytes(msgs))
            return True

        with mock.patch.object(emulator, "send_avatar_messages",
                               side_effect=capture_send):
            emulator.send_remote_vehicle(object(), observer, target)

        self.assertFalse(emulator.is_vehicle_visible_to(observer, target))
        self.assertNotIn(target["account_id"], observer["known_remote_accounts"])
        self.assertEqual(sent, [])

    def test_ally_is_visible_before_battle_period(self):
        observer = _make_combat_session(
            59, 1, (0.0, 0.0, 0.0),
            vehicle={"vehicleClass": "heavyTank"})
        target = _make_combat_session(
            60, 1, (0.0, 0.0, 1000.0),
            vehicle={"vehicleClass": "lightTank"})
        observer["battle_period_active"] = False
        target["battle_period_active"] = False

        self.assertTrue(emulator.is_vehicle_visible_to(observer, target))

    def test_minimap_positions_use_persistent_arena_indices(self):
        observer = _make_combat_session(54, 1, (10.0, 0.0, 20.0))
        hidden = _make_combat_session(55, 2, (100.0, 0.0, 100.0))
        visible = _make_combat_session(56, 2, (30.0, 0.0, 40.0))
        observer["arena_remote_accounts"] = {
            hidden["account_id"],
            visible["account_id"],
        }
        observer["known_remote_accounts"] = {visible["account_id"]}

        msg = emulator.build_minimap_positions_update(
            observer, [observer, hidden, visible])
        indices, positions = _parse_update_positions(msg)

        self.assertEqual(indices, [0, 2])
        self.assertEqual(positions, [10, 20, 30, 40])

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

    def test_match_countdown_waits_until_all_players_are_ready(self):
        s1 = _make_loading_session(1)
        s2 = _make_loading_session(2)
        s1.pop("battle_start_wall", None)
        s2.pop("battle_start_wall", None)
        s1["server_time_zero_wall"] = 1000.0
        s2["server_time_zero_wall"] = 1000.0
        emulator.active_battle_accounts[1] = s1
        emulator.active_battle_accounts[2] = s2
        scheduled = []
        sent = []
        arena_updates = []
        now = [1000.0]

        def capture_send(_sock, _addr, _sess, msgs, label, reliable=True):
            sent.append((_sess, bytes(msgs), label, reliable))
            return True

        def capture_arena(_sock, _addr, _sess, update_type, data, label):
            arena_updates.append((_sess, update_type, data, label))

        with mock.patch.object(emulator.time, "time", side_effect=lambda: now[0]), \
                mock.patch.object(emulator, "send_avatar_messages", side_effect=capture_send), \
                mock.patch.object(emulator, "send_avatar_arena_update", side_effect=capture_arena), \
                mock.patch.object(emulator, "runtime_call_later", side_effect=lambda delay, cb: scheduled.append((delay, cb))), \
                mock.patch.object(emulator, "start_battle_tick_loop", return_value=None):
            emulator.send_avatar_ready_and_prebattle(object(), s1["addr"], s1)
            self.assertEqual(scheduled, [])
            self.assertEqual(arena_updates, [])
            emulator.send_avatar_ready_and_prebattle(object(), s2["addr"], s2)
            self.assertEqual(len(scheduled), 1)
            self.assertAlmostEqual(scheduled[0][0],
                                   emulator.PREBATTLE_TIMER_SECONDS)
            now[0] = 1025.1
            scheduled[0][1]()

        self.assertTrue(s1["battle_client_ready"])
        self.assertTrue(s2["battle_client_ready"])
        self.assertTrue(s1["battle_period_active"])
        self.assertTrue(s2["battle_period_active"])
        self.assertTrue(s1["battle_period_timer_started"])
        self.assertTrue(s2["battle_period_timer_started"])
        self.assertEqual(len(arena_updates), 2)
        ready_updates = []
        for _sess, msgs, _label, _reliable in sent:
            for payload in _message_payloads(
                    msgs, emulator.AVATAR_UPDATEARENA_MSG_ID):
                if payload[4] == emulator.ARENA_UPDATE_AVATAR_READY:
                    ready_updates.append((_sess, payload))
        self.assertEqual(len(ready_updates), 4)
        battle_sends = [entry for entry in sent if "PERIOD=BATTLE" in entry[2]]
        self.assertEqual(len(battle_sends), 2)
        self.assertEqual({entry[0]["account_id"] for entry in battle_sends}, {1, 2})

    def test_ready_after_loading_delay_gets_full_prebattle_countdown(self):
        sess = _make_loading_session(1)
        sess.pop("battle_start_wall", None)
        sess["server_time_zero_wall"] = 1000.0
        emulator.active_battle_accounts[1] = sess
        arena_updates = []
        scheduled = []

        def capture_arena(_sock, _addr, _sess, update_type, data, label):
            arena_updates.append((_sess, update_type, data, label))

        with mock.patch.object(emulator.time, "time", return_value=1021.0), \
                mock.patch.object(emulator, "send_avatar_messages", return_value=True), \
                mock.patch.object(emulator, "send_avatar_arena_update", side_effect=capture_arena), \
                mock.patch.object(emulator, "runtime_call_later",
                                  side_effect=lambda delay, cb: scheduled.append((delay, cb))):
            emulator.send_avatar_ready_and_prebattle(object(), sess["addr"], sess)

        self.assertEqual(sess["battle_start_wall"], 1046.0)
        self.assertEqual(sess["battle_end_wall"], 1946.0)
        self.assertEqual(
            arena_updates[0][2],
            (emulator.ARENA_PERIOD_PREBATTLE, 43,
             emulator.PREBATTLE_TIMER_SECONDS, None))
        self.assertEqual(scheduled[0][0], 25.0)

    def test_second_ready_player_starts_shared_countdown(self):
        s1 = _make_loading_session(1)
        s2 = _make_loading_session(2)
        s1.pop("battle_start_wall", None)
        s2.pop("battle_start_wall", None)
        s1["server_time_zero_wall"] = 1000.0
        s2["server_time_zero_wall"] = 1000.0
        emulator.active_battle_accounts[1] = s1
        emulator.active_battle_accounts[2] = s2
        scheduled = []
        sent = []
        arena_updates = []
        now = [1000.0]

        def capture_send(_sock, _addr, _sess, msgs, label, reliable=True):
            sent.append((_sess, bytes(msgs), label, reliable))
            return True

        def capture_arena(_sock, _addr, _sess, update_type, data, label):
            arena_updates.append((_sess, update_type, data, label))

        with mock.patch.object(emulator.time, "time", side_effect=lambda: now[0]), \
                mock.patch.object(emulator, "send_avatar_messages", side_effect=capture_send), \
                mock.patch.object(emulator, "send_avatar_arena_update", side_effect=capture_arena), \
                mock.patch.object(emulator, "runtime_call_later",
                                  side_effect=lambda delay, cb: scheduled.append((delay, cb))), \
                mock.patch.object(emulator, "start_battle_tick_loop", return_value=None):
            emulator.send_avatar_ready_and_prebattle(object(), s1["addr"], s1)
            self.assertNotIn("battle_start_wall", s1)
            self.assertNotIn("battle_start_wall", s2)
            now[0] = 1010.0
            emulator.send_avatar_ready_and_prebattle(object(), s2["addr"], s2)
            self.assertEqual(s1["battle_start_wall"], 1035.0)
            self.assertEqual(s2["battle_start_wall"], 1035.0)
            self.assertEqual(len(scheduled), 1)
            now[0] = 1035.1
            scheduled[0][1]()

        battle_sends = [entry for entry in sent if "PERIOD=BATTLE" in entry[2]]
        self.assertEqual(len(battle_sends), 2)
        self.assertEqual({entry[0]["account_id"] for entry in battle_sends}, {1, 2})
        period_updates = [entry for entry in arena_updates
                          if entry[1] == emulator.ARENA_UPDATE_PERIOD]
        self.assertEqual(len(period_updates), 2)
        self.assertEqual(period_updates[0][2][1], 32)
        self.assertEqual(period_updates[1][2][1], 32)

    def test_prebattle_timer_default_is_25_seconds(self):
        self.assertEqual(emulator.PREBATTLE_TIMER_SECONDS, 25)
        self.assertEqual(emulator.BATTLE_PERIOD_TIME_OFFSET_SECONDS, 3.0)

    def test_ready_prebattle_period_uses_synced_start_wall(self):
        sess = _make_loading_session(1)
        sess["battle_start_wall"] = 1025.0
        sess["server_time_zero_wall"] = 980.0
        emulator.active_battle_accounts[1] = sess
        arena_updates = []
        scheduled = []

        def capture_arena(_sock, _addr, _sess, update_type, data, label):
            arena_updates.append((_sess, update_type, data, label))

        with mock.patch.object(emulator.time, "time", return_value=1000.0), \
                mock.patch.object(emulator, "send_avatar_messages", return_value=True), \
                mock.patch.object(emulator, "send_avatar_arena_update", side_effect=capture_arena), \
                mock.patch.object(emulator, "runtime_call_later",
                                  side_effect=lambda delay, cb: scheduled.append((delay, cb))):
            emulator.send_avatar_ready_and_prebattle(object(), sess["addr"], sess)

        self.assertEqual(len(arena_updates), 1)
        self.assertEqual(
            arena_updates[0][2],
            (emulator.ARENA_PERIOD_PREBATTLE, 42,
             emulator.PREBATTLE_TIMER_SECONDS, None))
        self.assertEqual(len(scheduled), 1)
        self.assertEqual(scheduled[0][0], 25.0)

    def test_prebattle_period_end_reaches_zero_at_scheduled_start(self):
        sess = _make_loading_session(1)
        sess.pop("battle_start_wall", None)
        sess["server_time_zero_wall"] = 1000.0
        emulator.active_battle_accounts[1] = sess
        arena_updates = []
        scheduled = []

        def capture_arena(_sock, _addr, _sess, update_type, data, label):
            arena_updates.append((_sess, update_type, data, label))

        with mock.patch.object(emulator.time, "time", return_value=1000.0), \
                mock.patch.object(emulator, "send_avatar_messages", return_value=True), \
                mock.patch.object(emulator, "send_avatar_arena_update", side_effect=capture_arena), \
                mock.patch.object(emulator, "runtime_call_later",
                                  side_effect=lambda delay, cb: scheduled.append((delay, cb))):
            emulator.send_avatar_ready_and_prebattle(object(), sess["addr"], sess)

        period_end = arena_updates[0][2][1]
        client_server_time_at_start = (
            sess["battle_start_wall"] -
            sess["server_time_zero_wall"] -
            emulator.BATTLE_PERIOD_TIME_OFFSET_SECONDS)
        self.assertEqual(period_end, int(client_server_time_at_start))

    def test_battle_period_start_messages_use_synced_end_wall(self):
        s1 = _make_loading_session(1)
        s2 = _make_loading_session(2)
        s1["server_time_zero_wall"] = 100.0
        s2["server_time_zero_wall"] = 130.0
        emulator.sync_match_battle_timers([s1, s2], start_wall=200.0)

        p1 = _arena_update_values(
            emulator.build_battle_period_start_messages(s1),
            emulator.ARENA_UPDATE_PERIOD)
        p2 = _arena_update_values(
            emulator.build_battle_period_start_messages(s2),
            emulator.ARENA_UPDATE_PERIOD)

        self.assertEqual(p1, (
            emulator.ARENA_PERIOD_BATTLE, 997,
            emulator.BATTLE_TIMER_SECONDS, None))
        self.assertEqual(p2, (
            emulator.ARENA_PERIOD_BATTLE, 967,
            emulator.BATTLE_TIMER_SECONDS, None))
        self.assertEqual(p1[1] + int(s1["server_time_zero_wall"]),
                         p2[1] + int(s2["server_time_zero_wall"]))

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
        self.assertFalse(runtime.outbound)

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

    def test_hidden_enemy_gets_no_intro_or_motion_packets(self):
        fake = _FakeBattleModule()
        observer = _make_session(account_id=3, period_active=True)
        source = _make_session(account_id=4, period_active=True)
        source["force_hidden_for"] = {observer["account_id"]}
        fake.active_battle_accounts[3] = observer
        fake.active_battle_accounts[4] = source
        runtime = EventLoopRuntime(fake)

        runtime._run_battle_tick_once(sock=object())

        self.assertNotIn((observer, source), fake.remote_vehicle_intros)
        remote_id = fake.get_remote_vehicle_id(source)
        ids_to_observer = []
        for send in fake.avatar_sends:
            if send["addr"] == observer["addr"]:
                ids_to_observer.extend(_detailed_position_entity_ids(send["msgs"]))
        self.assertNotIn(remote_id, ids_to_observer)

    def test_visible_enemy_gets_intro_once_and_motion_packets_after(self):
        fake = _FakeBattleModule()
        observer = _make_session(account_id=5, period_active=True)
        source = _make_session(account_id=6, period_active=True)
        fake.active_battle_accounts[5] = observer
        fake.active_battle_accounts[6] = source
        runtime = EventLoopRuntime(fake)

        runtime._run_battle_tick_once(sock=object())
        runtime._run_battle_tick_once(sock=object())

        self.assertEqual(fake.remote_vehicle_intros.count((observer, source)), 1)
        remote_id = fake.get_remote_vehicle_id(source)
        ids_to_observer = []
        for send in fake.avatar_sends:
            if send["addr"] == observer["addr"]:
                ids_to_observer.extend(_detailed_position_entity_ids(send["msgs"]))
        self.assertGreaterEqual(ids_to_observer.count(remote_id), 2)

    def test_losing_visibility_sends_leave_aoi_and_clears_known(self):
        fake = _FakeBattleModule()
        observer = _make_session(account_id=7, period_active=True)
        source = _make_session(account_id=8, period_active=True)
        observer["known_remote_accounts"].add(source["account_id"])
        source["force_hidden_for"] = {observer["account_id"]}
        fake.active_battle_accounts[7] = observer
        fake.active_battle_accounts[8] = source
        runtime = EventLoopRuntime(fake)

        runtime._run_battle_tick_once(sock=object())

        self.assertNotIn(source["account_id"], observer["known_remote_accounts"])
        leave_sends = [send for send in fake.avatar_sends
                       if send["addr"] == observer["addr"] and
                       send["msgs"] == b"LEAVE"]
        self.assertEqual(len(leave_sends), 1)
        remote_id = fake.get_remote_vehicle_id(source)
        ids_to_observer = []
        for send in fake.avatar_sends:
            if send["addr"] == observer["addr"]:
                ids_to_observer.extend(_detailed_position_entity_ids(send["msgs"]))
        self.assertNotIn(remote_id, ids_to_observer)

    def test_remote_vehicle_is_reintroduced_after_visibility_returns(self):
        fake = _FakeBattleModule()
        observer = _make_session(account_id=9, period_active=True)
        source = _make_session(account_id=10, period_active=True)
        observer["known_remote_accounts"].add(source["account_id"])
        source["force_hidden_for"] = {observer["account_id"]}
        fake.active_battle_accounts[9] = observer
        fake.active_battle_accounts[10] = source
        runtime = EventLoopRuntime(fake)

        runtime._run_battle_tick_once(sock=object())
        source["force_hidden_for"] = set()
        runtime._run_battle_tick_once(sock=object())

        self.assertIn(source["account_id"], observer["known_remote_accounts"])
        self.assertEqual(fake.remote_vehicle_intros.count((observer, source)), 1)

    def test_minimap_update_contains_only_currently_visible_remotes(self):
        fake = _FakeBattleModule()
        fake.enable_minimap_updates = True
        observer = _make_session(account_id=12, period_active=True)
        hidden = _make_session(account_id=13, period_active=True)
        visible = _make_session(account_id=14, period_active=True)
        hidden["force_hidden_for"] = {observer["account_id"]}
        fake.active_battle_accounts[12] = observer
        fake.active_battle_accounts[13] = hidden
        fake.active_battle_accounts[14] = visible
        runtime = EventLoopRuntime(fake)

        runtime._run_battle_tick_once(sock=object())

        updates = [entry for entry in fake.minimap_updates
                   if entry[0] is observer]
        self.assertTrue(updates)
        self.assertEqual(updates[-1][1], (12, 14))

    def test_minimap_update_drops_remote_after_visibility_loss(self):
        fake = _FakeBattleModule()
        fake.enable_minimap_updates = True
        observer = _make_session(account_id=15, period_active=True)
        source = _make_session(account_id=16, period_active=True)
        observer["known_remote_accounts"].add(source["account_id"])
        observer["arena_remote_accounts"] = {source["account_id"]}
        source["force_hidden_for"] = {observer["account_id"]}
        fake.active_battle_accounts[15] = observer
        fake.active_battle_accounts[16] = source
        runtime = EventLoopRuntime(fake)

        runtime._run_battle_tick_once(sock=object())

        updates = [entry for entry in fake.minimap_updates
                   if entry[0] is observer]
        self.assertTrue(updates)
        self.assertEqual(updates[-1][1], (15,))
        minimap_sends = [send for send in fake.avatar_sends
                         if send["addr"] == observer["addr"] and
                         b"MINIMAP" in send["msgs"]]
        self.assertTrue(minimap_sends)

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

    def test_client_vehicle_position_blocks_static_obstacle(self):
        original_cache = dict(emulator.STATIC_OBSTACLE_CACHE)
        original_index = dict(emulator.STATIC_OBSTACLE_INDEX_CACHE)
        arena_type_id = 2
        sess = _make_combat_session(405, 1, (-5.0, 0.0, 0.0))
        sess["battle_arena_type_id"] = arena_type_id
        sess["server_vehicle_authoritative"] = False
        sess["client_vehicle_pos"] = (-5.0, 0.0, 0.0)
        sess["client_vehicle_last_update_time"] = time.time() - 0.1
        try:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_INDEX_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE[arena_type_id] = [
                (0.0, 0.0, 0.0, 2.0, 4.0,
                 b"content/Buildings/testHouse.model",
                 ((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0)))
            ]

            with mock.patch.object(emulator, "sample_terrain",
                                   return_value=(0.0, (0.0, 1.0, 0.0))):
                emulator.record_client_vehicle_position(
                    sess, (5.0, 0.0, 0.0), 0.0)
        finally:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE.update(original_cache)
            emulator.STATIC_OBSTACLE_INDEX_CACHE.clear()
            emulator.STATIC_OBSTACLE_INDEX_CACHE.update(original_index)

        self.assertEqual(sess["client_vehicle_pos"], (-5.0, 0.0, 0.0))
        self.assertEqual(sess["battle_pos"], (-5.0, 0.0, 0.0))
        self.assertTrue(sess["battle_motion_force_position"])

    def test_vehicle_collision_blocks_tank_overlap(self):
        source = _make_combat_session(401, 1, (0.0, 0.0, 0.0))
        blocker = _make_combat_session(402, 2, (0.0, 0.0, 4.0))
        source["battle_match_id"] = 700
        blocker["battle_match_id"] = 700
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[blocker["account_id"]] = blocker

        new_x, new_z, blocked = emulator.resolve_motion_against_vehicles(
            source, 0.0, 0.0, 0.0, 4.0, tank_radius=2.5)

        self.assertTrue(blocked)
        self.assertEqual((new_x, new_z), (0.0, 0.0))

    def test_vehicle_collision_allows_driving_out_of_overlap(self):
        source = _make_combat_session(403, 1, (0.0, 0.0, 0.0))
        blocker = _make_combat_session(404, 2, (0.0, 0.0, 2.0))
        source["battle_match_id"] = 701
        blocker["battle_match_id"] = 701
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[blocker["account_id"]] = blocker

        new_x, new_z, blocked = emulator.resolve_motion_against_vehicles(
            source, 0.0, 0.0, 0.0, -2.0, tank_radius=2.5)

        self.assertFalse(blocked)
        self.assertEqual((new_x, new_z), (0.0, -2.0))

    def test_vehicle_collision_dimensions_use_visual_body_scale(self):
        half_width, half_length = emulator.vehicle_collision_dimensions(
            _make_combat_vehicle())

        self.assertAlmostEqual(half_width, 2.04)
        self.assertAlmostEqual(half_length, 2.86)

    def test_destroyed_vehicle_blocks_other_vehicle_motion(self):
        source = _make_combat_session(417, 1, (0.0, 0.0, 0.0))
        blocker = _make_combat_session(418, 2, (0.0, 0.0, 12.0), health=0)
        source["battle_match_id"] = 707
        blocker["battle_match_id"] = 707
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[blocker["account_id"]] = blocker

        new_x, new_z, blocked = emulator.resolve_motion_against_vehicles(
            source, 0.0, 0.0, 0.0, 20.0, yaw=0.0, tank_radius=1.0)

        self.assertTrue(blocked)
        self.assertEqual(new_x, 0.0)
        self.assertGreater(new_z, 6.15)
        self.assertLess(new_z, 6.45)

    def test_vehicle_collision_returns_contact_point_for_swept_overlap(self):
        source = _make_combat_session(413, 1, (0.0, 0.0, 0.0))
        blocker = _make_combat_session(414, 2, (0.0, 0.0, 12.0))
        source["battle_match_id"] = 706
        blocker["battle_match_id"] = 706
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[blocker["account_id"]] = blocker

        new_x, new_z, blocked = emulator.resolve_motion_against_vehicles(
            source, 0.0, 0.0, 0.0, 20.0, yaw=0.0, tank_radius=2.5)
        info = source.get("battle_vehicle_collision_info")

        self.assertTrue(blocked)
        self.assertEqual(new_x, 0.0)
        self.assertGreater(new_z, 6.15)
        self.assertLess(new_z, 6.45)
        self.assertIsNotNone(info)
        self.assertTrue(info.get("contact_confirmed"))
        self.assertGreater(info.get("contact_t"), 0.0)

    def test_destroyed_vehicle_tick_stays_frozen(self):
        sess = _make_combat_session(419, 1, (0.0, 0.0, 0.0), health=0)
        sess["battle_motion_flags"] = 1
        sess["battle_speed"] = 8.0
        sess["battle_rspeed"] = 1.0
        sess["battle_last_motion_time"] = time.time() - 1.0
        sess["server_vehicle_authoritative"] = False
        sess["client_vehicle_pos"] = (0.0, 0.0, 4.0)

        with mock.patch.object(emulator, "sample_terrain",
                               return_value=(0.0, (0.0, 1.0, 0.0))):
            pos, _yaw, speed, rspeed = emulator.advance_battle_motion(sess, 1)

        self.assertEqual(pos, (0.0, 0.0, 0.0))
        self.assertEqual(speed, 0.0)
        self.assertEqual(rspeed, 0.0)
        self.assertEqual(sess["battle_motion_flags"], 0)
        self.assertEqual(sess["battle_speed"], 0.0)
        self.assertEqual(sess["battle_rspeed"], 0.0)
        self.assertTrue(sess["server_vehicle_authoritative"])
        self.assertIsNone(sess["client_vehicle_pos"])

    def test_spg_hull_rotates_toward_aim_point_when_standing_still(self):
        artillery = _make_combat_vehicle()
        artillery.update({
            "vehicleClass": "SPG",
            "isSPG": True,
            "tags": frozenset(("SPG",)),
            "rotationSpeedLimit": math.radians(30.0),
            "chassisRotationSpeed": math.radians(30.0),
        })
        sess = _make_combat_session(801, 1, (0.0, 0.0, 0.0),
                                    vehicle=artillery)
        sess["battle_yaw"] = 0.0
        sess["battle_motion_flags"] = 0
        sess["battle_speed"] = 0.0
        sess["battle_rspeed"] = 0.0
        # Aim point is 50m ahead and 50m to the right - desired yaw atan2(50,50) = pi/4.
        sess["battle_target_pos"] = (50.0, 1.3, 50.0)
        sess["battle_last_motion_time"] = time.time() - 0.1

        with mock.patch.object(emulator, "sample_terrain",
                               return_value=(0.0, (0.0, 1.0, 0.0))):
            _pos, yaw, speed, rspeed = emulator.advance_battle_motion(sess, 0)

        self.assertAlmostEqual(speed, 0.0)
        self.assertGreater(rspeed, 0.0,
                           "Hull should rotate toward the aim point on the right")
        self.assertGreater(yaw, 0.0)

    def test_at_spg_hull_rotates_toward_aim_point_when_standing_still(self):
        td = _make_combat_vehicle()
        td.update({
            "vehicleClass": "AT-SPG",
            "isATSPG": True,
            "tags": frozenset(("AT-SPG",)),
            "rotationSpeedLimit": math.radians(35.0),
            "chassisRotationSpeed": math.radians(35.0),
        })
        sess = _make_combat_session(802, 1, (0.0, 0.0, 0.0), vehicle=td)
        sess["battle_yaw"] = 0.0
        sess["battle_motion_flags"] = 0
        sess["battle_speed"] = 0.0
        sess["battle_rspeed"] = 0.0
        # Aim point on the left - desired yaw negative.
        sess["battle_target_pos"] = (-50.0, 1.3, 50.0)
        sess["battle_last_motion_time"] = time.time() - 0.1

        with mock.patch.object(emulator, "sample_terrain",
                               return_value=(0.0, (0.0, 1.0, 0.0))):
            _pos, yaw, _speed, rspeed = emulator.advance_battle_motion(sess, 0)

        self.assertLess(rspeed, 0.0,
                        "AT-SPG hull should rotate left toward the aim point")
        self.assertLess(yaw, 0.0)

    def test_at_spg_targeting_keeps_turret_yaw_locked_to_hull(self):
        td = _make_combat_vehicle()
        td.update({
            "vehicleClass": "AT-SPG",
            "isATSPG": True,
            "tags": frozenset(("AT-SPG",)),
            "turretRotationSpeed": math.radians(90.0),
            "gunRotationSpeed": math.radians(90.0),
        })
        sess = _make_combat_session(806, 1, (0.0, 0.0, 0.0), vehicle=td)
        sess["battle_yaw"] = math.radians(15.0)
        sess["battle_turret_yaw"] = math.radians(15.0)
        sess["battle_targeting_state_time"] = time.time() - 0.2

        emulator.build_targeting_for_point(sess, (-50.0, 1.3, 50.0))

        self.assertAlmostEqual(sess["battle_turret_yaw"], sess["battle_yaw"])
        self.assertAlmostEqual(
            emulator.normalize_angle(
                math.atan2(sess["battle_shot_vec"][0],
                           sess["battle_shot_vec"][2]) -
                sess["battle_yaw"]),
            0.0)
        sess["battle_turret_yaw"] = math.radians(-80.0)
        shot_vec = emulator.get_session_current_gun_direction(sess)
        _yaw, _pitch, remote_turret_yaw = emulator.get_remote_vehicle_angles(sess)
        self.assertAlmostEqual(
            emulator.normalize_angle(
                math.atan2(shot_vec[0], shot_vec[2]) -
                sess["battle_yaw"]),
            0.0)
        self.assertAlmostEqual(remote_turret_yaw, 0.0)

    def test_spg_hull_does_not_rotate_when_aim_point_aligned(self):
        artillery = _make_combat_vehicle()
        artillery.update({
            "vehicleClass": "SPG",
            "isSPG": True,
            "tags": frozenset(("SPG",)),
            "rotationSpeedLimit": math.radians(30.0),
            "chassisRotationSpeed": math.radians(30.0),
        })
        sess = _make_combat_session(803, 1, (0.0, 0.0, 0.0),
                                    vehicle=artillery)
        sess["battle_yaw"] = 0.0
        sess["battle_motion_flags"] = 0
        sess["battle_speed"] = 0.0
        sess["battle_rspeed"] = 0.0
        # Aim point straight ahead - desired yaw 0 - within dead band.
        sess["battle_target_pos"] = (0.0, 1.3, 100.0)
        sess["battle_last_motion_time"] = time.time() - 0.1

        with mock.patch.object(emulator, "sample_terrain",
                               return_value=(0.0, (0.0, 1.0, 0.0))):
            _pos, yaw, _speed, rspeed = emulator.advance_battle_motion(sess, 0)

        self.assertAlmostEqual(rspeed, 0.0,
                               msg="No hull rotation when already aligned")
        self.assertAlmostEqual(yaw, 0.0)

    def test_medium_tank_hull_does_not_autorotate_toward_aim_point(self):
        medium = _make_combat_vehicle()
        medium.update({
            "vehicleClass": "mediumTank",
            "tags": frozenset(("mediumTank",)),
            "rotationSpeedLimit": math.radians(40.0),
            "chassisRotationSpeed": math.radians(40.0),
        })
        sess = _make_combat_session(804, 1, (0.0, 0.0, 0.0), vehicle=medium)
        sess["battle_yaw"] = 0.0
        sess["battle_motion_flags"] = 0
        sess["battle_speed"] = 0.0
        sess["battle_rspeed"] = 0.0
        sess["battle_target_pos"] = (50.0, 1.3, 50.0)
        sess["battle_last_motion_time"] = time.time() - 0.1

        with mock.patch.object(emulator, "sample_terrain",
                               return_value=(0.0, (0.0, 1.0, 0.0))):
            _pos, yaw, _speed, rspeed = emulator.advance_battle_motion(sess, 0)

        self.assertAlmostEqual(rspeed, 0.0,
                               msg="Medium tank must not rotate without input")
        self.assertAlmostEqual(yaw, 0.0)

    def test_spg_hull_does_not_autorotate_while_moving(self):
        artillery = _make_combat_vehicle()
        artillery.update({
            "vehicleClass": "SPG",
            "isSPG": True,
            "tags": frozenset(("SPG",)),
            "rotationSpeedLimit": math.radians(30.0),
            "chassisRotationSpeed": math.radians(30.0),
        })
        sess = _make_combat_session(805, 1, (0.0, 0.0, 0.0),
                                    vehicle=artillery)
        sess["battle_yaw"] = 0.0
        sess["battle_motion_flags"] = 1  # forward
        sess["battle_speed"] = 5.0  # already moving
        sess["battle_rspeed"] = 0.0
        sess["battle_target_pos"] = (50.0, 1.3, 50.0)
        sess["battle_last_motion_time"] = time.time() - 0.1

        with mock.patch.object(emulator, "sample_terrain",
                               return_value=(0.0, (0.0, 1.0, 0.0))):
            _pos, _yaw, _speed, rspeed = emulator.advance_battle_motion(sess, 1)

        self.assertAlmostEqual(rspeed, 0.0,
                               msg="No autorotation while moving forward")

    def test_spg_hull_autorotation_disabled_by_config_flag(self):
        artillery = _make_combat_vehicle()
        artillery.update({
            "vehicleClass": "SPG",
            "isSPG": True,
            "tags": frozenset(("SPG",)),
            "rotationSpeedLimit": math.radians(30.0),
            "chassisRotationSpeed": math.radians(30.0),
        })
        sess = _make_combat_session(806, 1, (0.0, 0.0, 0.0),
                                    vehicle=artillery)
        sess["battle_yaw"] = 0.0
        sess["battle_motion_flags"] = 0
        sess["battle_speed"] = 0.0
        sess["battle_rspeed"] = 0.0
        sess["battle_target_pos"] = (50.0, 1.3, 50.0)
        sess["battle_last_motion_time"] = time.time() - 0.1

        original_flag = emulator.HULL_AIM_AUTOROTATION_ENABLED
        try:
            emulator.HULL_AIM_AUTOROTATION_ENABLED = False
            with mock.patch.object(emulator, "sample_terrain",
                                   return_value=(0.0, (0.0, 1.0, 0.0))):
                _pos, _yaw, _speed, rspeed = emulator.advance_battle_motion(
                    sess, 0)
        finally:
            emulator.HULL_AIM_AUTOROTATION_ENABLED = original_flag

        self.assertAlmostEqual(rspeed, 0.0)

    def test_destroyed_vehicle_move_input_is_ignored(self):
        sess = _make_combat_session(420, 1, (0.0, 0.0, 0.0), health=0)
        sess["battle_motion_flags"] = 0
        sess["battle_speed"] = 5.0
        sess["battle_rspeed"] = 0.5
        sess["battle_client_control_enabled"] = True
        move_id = next(iter(emulator.AVATAR_BASE_METHOD_VEHICLE_MOVE_WITH_IDS))
        payload = b"\x00\x00\x00\x00\x01"

        with mock.patch.object(emulator, "send_avatar_messages", return_value=True) as send_mock:
            handled = emulator.handle_avatar_base_method(
                object(), sess["addr"], sess, move_id, payload)

        self.assertTrue(handled)
        self.assertEqual(sess["battle_motion_flags"], 0)
        self.assertEqual(sess["battle_speed"], 0.0)
        self.assertEqual(sess["battle_rspeed"], 0.0)
        self.assertFalse(sess["battle_client_control_enabled"])
        send_mock.assert_called()

    def test_ram_damage_formula_favors_heavier_vehicle(self):
        light = _make_combat_vehicle(health=290, weight_kg=17530.0)
        heavy = _make_combat_vehicle(health=3200, weight_kg=188980.0)

        self.assertEqual(
            emulator.compute_ram_damage(light, heavy, 5.0),
            (0, 0))

        damage_to_heavy, damage_to_light = emulator.compute_ram_damage(
            light, heavy, 10.0)
        damage_to_light_from_heavy, damage_to_heavy_self = emulator.compute_ram_damage(
            heavy, light, 12.0)

        self.assertGreater(damage_to_light, 150)
        self.assertLess(damage_to_light, 290)
        self.assertGreater(damage_to_light, damage_to_heavy)
        self.assertGreater(damage_to_light_from_heavy, damage_to_heavy_self)
        self.assertGreater(damage_to_light_from_heavy, damage_to_heavy)

    def test_slow_vehicle_collision_blocks_without_ram_damage(self):
        source = _make_combat_session(
            405, 1, (0.0, 0.0, 0.0),
            health=500,
            vehicle=_make_combat_vehicle(health=500, weight_kg=30000.0))
        blocker = _make_combat_session(
            406, 2, (0.0, 0.0, 12.0),
            health=500,
            vehicle=_make_combat_vehicle(health=500, weight_kg=30000.0))
        source["battle_match_id"] = 702
        blocker["battle_match_id"] = 702
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[blocker["account_id"]] = blocker

        _new_x, _new_z, blocked = emulator.resolve_motion_against_vehicles(
            source, 0.0, 0.0, 0.0, 20.0, yaw=0.0, source_speed=5.0)
        source["battle_pos"] = (_new_x, 0.0, _new_z)
        with mock.patch.object(emulator, "send_avatar_messages", return_value=True):
            processed = emulator.process_pending_vehicle_collision(object(), source)

        self.assertTrue(blocked)
        self.assertFalse(processed)
        self.assertEqual(source["battle_vehicle_health"], 500)
        self.assertEqual(blocker["battle_vehicle_health"], 500)

    def test_ram_damage_requires_confirmed_contact_point(self):
        source = _make_combat_session(
            415, 1, (0.0, 0.0, 0.0),
            health=500,
            vehicle=_make_combat_vehicle(health=500, weight_kg=60000.0))
        target = _make_combat_session(
            416, 2, (0.0, 0.0, 12.0),
            health=500,
            vehicle=_make_combat_vehicle(health=500, weight_kg=10000.0))
        collision_info = {
            "session": target,
            "normal": (0.0, 1.0),
            "source_yaw": 0.0,
            "source_speed": 10.0,
        }

        with mock.patch.object(emulator, "send_avatar_messages", return_value=True):
            processed = emulator.process_ram_collision(object(), source, target, collision_info)

        self.assertFalse(processed)
        self.assertEqual(source["battle_vehicle_health"], 500)
        self.assertEqual(target["battle_vehicle_health"], 500)

    def test_enemy_ram_damages_both_tanks_and_forces_positions(self):
        source = _make_combat_session(
            407, 1, (0.0, 0.0, 0.0),
            health=500,
            vehicle=_make_combat_vehicle(health=500, weight_kg=60000.0))
        target = _make_combat_session(
            408, 2, (0.0, 0.0, 12.0),
            health=500,
            vehicle=_make_combat_vehicle(health=500, weight_kg=10000.0))
        source["battle_match_id"] = 703
        target["battle_match_id"] = 703
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[target["account_id"]] = target
        sent = []

        def capture_send(_sock, _addr, _sess, msgs, _label, reliable=True):
            sent.append((_sess, bytes(msgs), reliable))
            return True

        with mock.patch.object(emulator, "send_avatar_messages", side_effect=capture_send), \
                mock.patch.object(emulator, "sample_terrain", return_value=(0.0, (0.0, 1.0, 0.0))):
            _new_x, _new_z, blocked = emulator.resolve_motion_against_vehicles(
                source, 0.0, 0.0, 0.0, 20.0, yaw=0.0, source_speed=10.0)
            source["battle_pos"] = (_new_x, 0.0, _new_z)
            processed = emulator.process_pending_vehicle_collision(object(), source)

        self.assertTrue(blocked)
        self.assertTrue(processed)
        self.assertLess(target["battle_vehicle_health"], 500)
        self.assertLess(source["battle_vehicle_health"], 500)
        self.assertGreater(source["battle_damage_dealt"], target["battle_damage_dealt"])
        forced_sends = [
            entry for entry in sent
            if entry[1][:1] == bytes([emulator.CLIENT_FORCED_POSITION_MSG_ID])
        ]
        self.assertGreaterEqual(len(forced_sends), 2)

    def test_friendly_ram_forces_positions_without_health_damage(self):
        source = _make_combat_session(
            409, 1, (0.0, 0.0, 0.0),
            health=500,
            vehicle=_make_combat_vehicle(health=500, weight_kg=60000.0))
        target = _make_combat_session(
            410, 1, (0.0, 0.0, 12.0),
            health=500,
            vehicle=_make_combat_vehicle(health=500, weight_kg=10000.0))
        source["battle_match_id"] = 704
        target["battle_match_id"] = 704
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[target["account_id"]] = target
        sent = []

        def capture_send(_sock, _addr, _sess, msgs, _label, reliable=True):
            sent.append((_sess, bytes(msgs), reliable))
            return True

        with mock.patch.object(emulator, "send_avatar_messages", side_effect=capture_send), \
                mock.patch.object(emulator, "sample_terrain", return_value=(0.0, (0.0, 1.0, 0.0))):
            _new_x, _new_z, _blocked = emulator.resolve_motion_against_vehicles(
                source, 0.0, 0.0, 0.0, 20.0, yaw=0.0, source_speed=10.0)
            source["battle_pos"] = (_new_x, 0.0, _new_z)
            processed = emulator.process_pending_vehicle_collision(object(), source)

        self.assertTrue(processed)
        self.assertEqual(source["battle_vehicle_health"], 500)
        self.assertEqual(target["battle_vehicle_health"], 500)
        forced_sends = [
            entry for entry in sent
            if entry[1][:1] == bytes([emulator.CLIENT_FORCED_POSITION_MSG_ID])
        ]
        self.assertGreaterEqual(len(forced_sends), 2)

    def test_ram_cooldown_prevents_damage_every_tick(self):
        source = _make_combat_session(
            411, 1, (0.0, 0.0, 0.0),
            health=500,
            vehicle=_make_combat_vehicle(health=500, weight_kg=60000.0))
        target = _make_combat_session(
            412, 2, (0.0, 0.0, 12.0),
            health=500,
            vehicle=_make_combat_vehicle(health=500, weight_kg=10000.0))
        source["battle_match_id"] = 705
        target["battle_match_id"] = 705
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[target["account_id"]] = target
        collision_info = {
            "session": target,
            "normal": (0.0, 1.0),
            "source_yaw": 0.0,
            "source_speed": 10.0,
            "contact_pos": (0.0, 6.3),
            "damage_contact_pos": (0.0, 6.3),
            "contact_confirmed": True,
        }

        with mock.patch.object(emulator, "send_avatar_messages", return_value=True), \
                mock.patch.object(emulator, "sample_terrain", return_value=(0.0, (0.0, 1.0, 0.0))), \
                mock.patch.object(emulator.time, "time", side_effect=[1000.0, 1000.1]):
            first = emulator.process_ram_collision(object(), source, target, collision_info)
            after_first = (source["battle_vehicle_health"],
                           target["battle_vehicle_health"])
            second = emulator.process_ram_collision(object(), source, target, collision_info)

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(
            (source["battle_vehicle_health"], target["battle_vehicle_health"]),
            after_first)

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

    def test_static_obstacle_in_front_of_target_blocks_shot_with_default_target_gap(self):
        """Regression: a stone right in front of the target tank (within the
        default target_gap=4m of the impact point) used to be filtered out by
        the 3D-sphere target_gap filter, so the shot would pass through it.
        After the fix, the target_gap filter is directional and only skips
        stones AT or PAST the target along the ray, so the stone in front
        properly blocks the shot."""
        original_cache = dict(emulator.STATIC_OBSTACLE_CACHE)
        source = _make_combat_session(82, 1, (0.0, 0.0, 0.0))
        source["battle_arena_type_id"] = emulator.ARENA_TYPE_KARELIA
        try:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            # Stone 3m in front of the target tank (target endpoint at z=20).
            # Stone center at z=17, shot_radius=4 => stone extends to z=21,
            # which under the OLD 3D-sphere filter (radius+target_gap=8)
            # was within the filter's exclusion sphere and got skipped.
            emulator.STATIC_OBSTACLE_CACHE[emulator.ARENA_TYPE_KARELIA] = [
                (0.0, 1.0, 17.0, 3.0, 4.0,
                 b"content/Environment/Rocks/frontStone.model"),
            ]
            blocked = emulator.ray_static_obstacle_hit(
                source, (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), 20.0)
        finally:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE.update(original_cache)

        self.assertIsNotNone(
            blocked,
            "Stone in front of target tank must block the shot even when its "
            "3D distance to the impact point is within the default target_gap")

    def test_static_obstacle_at_or_past_target_is_skipped_by_target_gap(self):
        """Counterpart: a stone right behind the target (e.g., a wall the
        tank is parked against) must still be filtered by target_gap so that
        legitimate shots at the tank are not falsely blocked."""
        original_cache = dict(emulator.STATIC_OBSTACLE_CACHE)
        source = _make_combat_session(83, 1, (0.0, 0.0, 0.0))
        source["battle_arena_type_id"] = emulator.ARENA_TYPE_KARELIA
        try:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            # Stone 2m past the target endpoint (target at z=20, stone at z=22).
            emulator.STATIC_OBSTACLE_CACHE[emulator.ARENA_TYPE_KARELIA] = [
                (0.0, 1.0, 22.0, 3.0, 4.0,
                 b"content/Environment/Rocks/wallStone.model"),
            ]
            blocked = emulator.ray_static_obstacle_hit(
                source, (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), 20.0)
        finally:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE.update(original_cache)

        self.assertIsNone(
            blocked,
            "Stone behind the target (the wall it is parked against) must be "
            "skipped by the directional target_gap filter")

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

    def test_matchmaking_queue_stats_reports_only_real_players_by_default(self):
        original_queue = list(emulator.matchmaking_queue)
        original_fillers = emulator.MATCHMAKING_QUEUE_FAKE_FILLERS
        try:
            sess = _make_combat_session(901, 1, (0.0, 0.0, 0.0),
                                        vehicle={
                                            "vehicleClass": "SPG",
                                            "tags": ["SPG"],
                                            "level": 8,
                                        })
            emulator.matchmaking_queue[:] = [{"addr": sess["addr"], "sess": sess}]
            emulator.MATCHMAKING_QUEUE_FAKE_FILLERS = 0

            length, levels, classes = emulator.get_matchmaking_queue_stats()

            self.assertEqual(length, 1)
            self.assertEqual(levels[8], 1)
            self.assertEqual(sum(levels), 1)
            self.assertEqual(classes[4], 1)
            self.assertEqual(sum(classes), 1)
        finally:
            emulator.matchmaking_queue[:] = original_queue
            emulator.MATCHMAKING_QUEUE_FAKE_FILLERS = original_fillers

    def test_matchmaking_queue_stats_uses_loaded_vehicle_level(self):
        original_queue = list(emulator.matchmaking_queue)
        original_fillers = emulator.MATCHMAKING_QUEUE_FAKE_FILLERS
        vehicles = {v["name"]: v for v in emulator.load_all_vehicles()}
        try:
            sess = _make_combat_session(903, 1, (0.0, 0.0, 0.0),
                                        vehicle=vehicles["Object_261"])
            emulator.matchmaking_queue[:] = [{"addr": sess["addr"], "sess": sess}]
            emulator.MATCHMAKING_QUEUE_FAKE_FILLERS = 0

            length, levels, classes = emulator.get_matchmaking_queue_stats()

            self.assertEqual(length, 1)
            self.assertEqual(levels[8], 1)
            self.assertEqual(sum(levels), 1)
            self.assertEqual(classes[4], 1)
            self.assertEqual(sum(classes), 1)
        finally:
            emulator.matchmaking_queue[:] = original_queue
            emulator.MATCHMAKING_QUEUE_FAKE_FILLERS = original_fillers

    def test_matchmaking_queue_stats_adds_configured_fake_fillers(self):
        original_queue = list(emulator.matchmaking_queue)
        original_fillers = emulator.MATCHMAKING_QUEUE_FAKE_FILLERS
        try:
            sess = _make_combat_session(902, 1, (0.0, 0.0, 0.0),
                                        vehicle={
                                            "vehicleClass": "mediumTank",
                                            "tags": ["mediumTank"],
                                            "level": 5,
                                        })
            emulator.matchmaking_queue[:] = [{"addr": sess["addr"], "sess": sess}]
            emulator.MATCHMAKING_QUEUE_FAKE_FILLERS = 4

            length, _levels, _classes = emulator.get_matchmaking_queue_stats()

            self.assertEqual(length, 5)
        finally:
            emulator.matchmaking_queue[:] = original_queue
            emulator.MATCHMAKING_QUEUE_FAKE_FILLERS = original_fillers

    def test_vehicle_lock_diff_matches_client_cache_shape(self):
        self.assertEqual(
            emulator.build_vehicle_lock_diff(7, emulator.LOCK_REASON_IN_QUEUE),
            {"cache": {"vehsLock": {7: emulator.LOCK_REASON_IN_QUEUE}}},
        )
        self.assertEqual(
            emulator.build_vehicle_lock_diff(7, None),
            {"cache": {"vehsLock": {7: None}}},
        )

    def test_enqueue_pushes_vehicle_queue_lock(self):
        sess = {
            "account_id": 771,
            "username": "queued",
            "addr": ("127.0.0.1", 1771),
        }
        sock = object()
        addr = sess["addr"]
        payload = struct.pack(
            "<hhiii", 0, emulator.CMD_ENQUEUE_FOR_ARENA, 1,
            emulator.ARENA_TYPE_KARELIA, 0)

        with mock.patch.object(emulator, "store_battle_entry",
                               return_value=91), \
                mock.patch.object(emulator, "send_account_event",
                                  return_value=True) as send_event, \
                mock.patch.object(emulator, "enqueue_for_matchmaking",
                                  return_value=None) as enqueue, \
                mock.patch.object(emulator, "push_account_diff",
                                  return_value=True) as push_diff:
            emulator.handle_account_doCmd(sock, addr, sess, 0xc4, payload)

        send_event.assert_called_once_with(
            sock, addr, sess, emulator.ACCOUNT_ONENQUEUED_MSG_ID,
            "Account.onEnqueued()")
        push_diff.assert_called_once_with(
            sock, addr, sess,
            {"cache": {"vehsLock": {1: emulator.LOCK_REASON_IN_QUEUE}}})
        enqueue.assert_called_once_with(sock, addr, sess)
        self.assertEqual(sess["battle_vehicle_inv_id"], 1)
        self.assertEqual(sess["battle_id"], 91)

    def test_dequeue_pushes_vehicle_unlock_and_cancels_empty_queue_timer(self):
        class FakeTimer:
            def __init__(self):
                self.cancelled = False

            def cancel(self):
                self.cancelled = True

        original_queue = list(emulator.matchmaking_queue)
        original_timer = emulator.matchmaking_timer
        sess = {
            "account_id": 772,
            "username": "queued",
            "addr": ("127.0.0.1", 1772),
            "battle_vehicle_inv_id": 4,
            "queued_for_battle": True,
        }
        sock = object()
        addr = sess["addr"]
        timer = FakeTimer()
        payload = struct.pack("<hhiii", 0, emulator.CMD_DEQUEUE, 0, 0, 0)
        try:
            emulator.matchmaking_queue[:] = [{"addr": addr, "sess": sess}]
            emulator.matchmaking_timer = timer
            emulator.active_battle_accounts[sess["account_id"]] = sess

            with mock.patch.object(emulator, "broadcast_account_server_counters",
                                   return_value=None) as counters, \
                    mock.patch.object(emulator, "send_account_event",
                                      return_value=True) as send_event, \
                    mock.patch.object(emulator, "push_account_diff",
                                      return_value=True) as push_diff:
                emulator.handle_account_doCmd(sock, addr, sess, 0xc4, payload)
        finally:
            emulator.matchmaking_queue[:] = original_queue
            emulator.matchmaking_timer = original_timer

        self.assertEqual(emulator.matchmaking_queue, original_queue)
        self.assertIs(emulator.matchmaking_timer, original_timer)
        self.assertFalse(sess["queued_for_battle"])
        self.assertTrue(timer.cancelled)
        self.assertNotIn(sess["account_id"], emulator.active_battle_accounts)
        counters.assert_called_once_with(sock)
        send_event.assert_called_once_with(
            sock, addr, sess, emulator.ACCOUNT_ONDEQUEUED_MSG_ID,
            "Account.onDequeued()")
        push_diff.assert_called_once_with(
            sock, addr, sess, {"cache": {"vehsLock": {4: None}}})

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

    def test_matchmaker_launch_sets_shared_launch_wall_without_starting_countdown(self):
        original_queue = list(emulator.matchmaking_queue)
        original_timer = emulator.matchmaking_timer
        original_next = emulator.next_battle_id
        original_cache = dict(emulator.STATIC_OBSTACLE_CACHE)
        s1 = _make_combat_session(311, 1, (0.0, 0.0, 0.0))
        s2 = _make_combat_session(312, 2, (0.0, 0.0, 0.0))
        s1["queued_for_battle"] = True
        s2["queued_for_battle"] = True
        s1["battle_arena_type_id"] = emulator.ARENA_TYPE_KARELIA
        s2["battle_arena_type_id"] = emulator.ARENA_TYPE_KARELIA
        scheduled = []
        sent_players = []
        try:
            emulator.matchmaking_queue[:] = [
                {"addr": s1["addr"], "sess": s1},
                {"addr": s2["addr"], "sess": s2},
            ]
            emulator.matchmaking_timer = None
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE[emulator.ARENA_TYPE_KARELIA] = [
                (1.0, 0.0, 0.0, 1.0, 1.0, b"stone.model")
            ]

            def capture_player(_sock, _addr, sess):
                sent_players.append(sess)

            with mock.patch.object(emulator.random, "uniform", return_value=0.0), \
                    mock.patch.object(emulator.time, "time", return_value=1000.0), \
                    mock.patch.object(emulator, "runtime_call_later",
                                      side_effect=lambda delay, cb: scheduled.append((delay, cb))), \
                    mock.patch.object(emulator, "send_account_event", return_value=None), \
                    mock.patch.object(emulator, "send_avatar_player", side_effect=capture_player):
                emulator.start_matchmaking_timer(object())
                self.assertEqual(len(scheduled), 1)
                scheduled[0][1]()
        finally:
            emulator.matchmaking_queue[:] = original_queue
            emulator.matchmaking_timer = original_timer
            emulator.next_battle_id = original_next
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE.update(original_cache)

        self.assertEqual(sent_players, [s1, s2])
        self.assertEqual(s1["battle_launch_wall"], 1000.0)
        self.assertEqual(s2["battle_launch_wall"], 1000.0)
        self.assertNotIn("battle_start_wall", s1)
        self.assertNotIn("battle_start_wall", s2)
        self.assertNotIn("battle_end_wall", s1)
        self.assertNotIn("battle_end_wall", s2)
        self.assertEqual(s1["battle_roster_sessions"], [s1, s2])
        self.assertEqual(s2["battle_roster_sessions"], [s1, s2])

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

    def test_remote_vehicle_messages_use_actual_team(self):
        observer = _make_combat_session(501, 2, (0.0, 0.0, 0.0))
        remote = _make_combat_session(502, 1, (0.0, 0.0, 10.0))
        compact = emulator.get_vehicle_compact_descr()
        remote["battle_vehicle"]["compactDescr"] = compact
        remote["battle_vehicle_compactDescr"] = compact

        msgs = emulator.build_remote_vehicle_messages(observer, remote)
        veh_info = _arena_update_values(
            msgs, emulator.ARENA_UPDATE_VEHICLE_ADDED)

        self.assertEqual(veh_info[3], 1)

    def test_initial_vehicle_list_contains_match_roster_before_visibility(self):
        viewer = _make_combat_session(521, 1, (0.0, 0.0, 0.0))
        enemy = _make_combat_session(522, 2, (500.0, 0.0, 500.0))
        compact = emulator.get_vehicle_compact_descr()
        for sess in (viewer, enemy):
            sess["battle_match_id"] = 9001
            sess["battle_period_active"] = False
            sess["battle_client_ready"] = False
            sess["battle_vehicle"]["compactDescr"] = compact
            sess["battle_vehicle_compactDescr"] = compact

        roster = emulator.build_match_vehicle_roster_for_viewer(
            viewer, [viewer, enemy])
        stats = emulator.build_match_vehicle_statistics_for_viewer(
            viewer, [viewer, enemy])
        msgs = emulator.build_avatar_player_bundle(
            vehicle_compact_descr=compact,
            vehicle_data=viewer["battle_vehicle"],
            player_name=viewer["username"],
            team=viewer["battle_team"],
            vehicle_list=roster,
            statistics_list=stats)

        vehicle_list = _arena_update_values(
            msgs, emulator.ARENA_UPDATE_VEHICLE_LIST)
        statistics = _arena_update_values(
            msgs, emulator.ARENA_UPDATE_STATISTICS)
        enemy_id = emulator.get_remote_vehicle_id(enemy)
        by_id = {info[0]: info for info in vehicle_list}

        self.assertEqual({emulator.PLAYER_VEHICLE_ID, enemy_id},
                         set(by_id.keys()))
        self.assertEqual(by_id[emulator.PLAYER_VEHICLE_ID][2],
                         viewer["username"])
        self.assertEqual(by_id[enemy_id][2], enemy["username"])
        self.assertEqual(by_id[enemy_id][3], 2)
        self.assertEqual(set(statistics),
                         {(emulator.PLAYER_VEHICLE_ID, 0), (enemy_id, 0)})

    def test_base_capture_updates_use_actual_base_team(self):
        viewer = _make_combat_session(503, 2, (0.0, 0.0, 0.0))
        sent = []

        def capture_send(_sock, _addr, _sess, msgs, _label, reliable=True):
            sent.append(bytes(msgs))
            return True

        with mock.patch.object(emulator, "send_avatar_messages",
                               side_effect=capture_send):
            emulator.send_base_capture_updates(
                object(), [viewer], [(2, 2, 35)])

        update = _arena_update_values(
            sent[0], emulator.ARENA_UPDATE_BASE_POINTS)
        self.assertEqual(update, (2, 2, 35))

    def test_display_team_keeps_actual_team_for_client_map_flags(self):
        viewer = _make_combat_session(506, 2, (0.0, 0.0, 0.0))
        ally = _make_combat_session(507, 2, (0.0, 0.0, 5.0))
        enemy = _make_combat_session(508, 1, (0.0, 0.0, 10.0))

        self.assertEqual(emulator.get_display_team(viewer, ally), 2)
        self.assertEqual(emulator.get_display_team(viewer, enemy), 1)

    def test_shot_dispersion_miss_stays_inside_dispersion_circle(self):
        source = _make_combat_session(504, 1, (0.0, 0.0, 40.0))
        shell = _make_shell(compact=9201, speed=500.0, gravity=9.81)
        source["battle_vehicle"].update({
            "defaultAmmo": [9201, 1],
            "shells": [shell],
        })
        source["battle_current_shell"] = 9201
        source["battle_ammo_stock"] = {9201: 1}
        source["battle_target_pos"] = (0.0, 1.3, 20.0)
        source["battle_target_pos_time"] = time.time()
        old_enabled = emulator.SHOT_DISPERSION_ENABLED
        old_chance = emulator.SHOT_HIT_CHANCE_PERCENT
        try:
            emulator.SHOT_DISPERSION_ENABLED = True
            emulator.SHOT_HIT_CHANCE_PERCENT = 0.0
            with mock.patch.object(emulator.random, "random", return_value=1.0), \
                    mock.patch.object(emulator.random, "uniform",
                                      side_effect=[
                                          0.0,
                                          (emulator.SHOT_TANK_HIT_RADIUS_H + 0.75) ** 2,
                                      ]):
                _msgs, _reload_time, _shell_cd, fired = (
                    emulator.build_vehicle_shot_messages(source))
        finally:
            emulator.SHOT_DISPERSION_ENABLED = old_enabled
            emulator.SHOT_HIT_CHANCE_PERCENT = old_chance

        self.assertTrue(fired)
        radius = emulator.shot_dispersion_radius(
            (0.0, 2.0, 40.0), (0.0, 1.3, 20.0))
        effective_radius = emulator.shot_dispersion_effective_radius(
            (0.0, 2.0, 40.0), (0.0, 1.3, 20.0))
        offset = abs(source["battle_last_shot_target_pos"][0])
        self.assertLessEqual(offset, radius)
        self.assertLessEqual(offset, effective_radius)
        self.assertTrue(source["battle_last_shot_forced_miss"])

    def test_targeting_marker_uses_configured_dispersion_radius(self):
        source = _make_combat_session(509, 1, (0.0, 0.0, 0.0))
        target_pos = (0.0, 1.3, 100.0)
        old_enabled = emulator.SHOT_DISPERSION_ENABLED
        try:
            emulator.SHOT_DISPERSION_ENABLED = True
            msgs = emulator.build_targeting_for_point(source, target_pos)
        finally:
            emulator.SHOT_DISPERSION_ENABLED = old_enabled

        payloads = _message_payloads(
            msgs, emulator.AVATAR_UPDATE_GUN_MARKER_MSG_ID)
        self.assertEqual(len(payloads), 1)
        shot_pos = struct.unpack_from("<fff", payloads[0], 4)
        angle = struct.unpack_from("<f", payloads[0], 28)[0]
        dx = target_pos[0] - shot_pos[0]
        dy = target_pos[1] - shot_pos[1]
        dz = target_pos[2] - shot_pos[2]
        distance = math.sqrt(dx * dx + dy * dy + dz * dz)
        visible_radius = math.tan(angle) * distance
        self.assertAlmostEqual(
            visible_radius,
            emulator.shot_dispersion_radius(shot_pos, target_pos),
            places=5)

    def test_shot_dispersion_uses_smaller_server_radius_than_marker(self):
        source = _make_combat_session(510, 1, (0.0, 0.0, 0.0))
        shell = _make_shell(compact=9203, speed=500.0, gravity=9.81)
        source["battle_vehicle"].update({
            "defaultAmmo": [9203, 1],
            "shells": [shell],
        })
        source["battle_current_shell"] = 9203
        source["battle_ammo_stock"] = {9203: 1}
        source["battle_target_pos"] = (0.0, 1.3, 100.0)
        source["battle_target_pos_time"] = time.time()
        old_enabled = emulator.SHOT_DISPERSION_ENABLED
        old_chance = emulator.SHOT_HIT_CHANCE_PERCENT
        old_scale = emulator.SHOT_DISPERSION_SERVER_RADIUS_SCALE
        old_bias = emulator.SHOT_DISPERSION_CENTER_BIAS
        try:
            emulator.SHOT_DISPERSION_ENABLED = True
            emulator.SHOT_HIT_CHANCE_PERCENT = 100.0
            emulator.SHOT_DISPERSION_SERVER_RADIUS_SCALE = 0.4
            emulator.SHOT_DISPERSION_CENTER_BIAS = 1.0
            with mock.patch.object(emulator.random, "random", return_value=0.0), \
                    mock.patch.object(emulator.random, "uniform",
                                      side_effect=[0.0, 1.0]):
                _msgs, _reload_time, _shell_cd, fired = (
                    emulator.build_vehicle_shot_messages(source))
        finally:
            emulator.SHOT_DISPERSION_ENABLED = old_enabled
            emulator.SHOT_HIT_CHANCE_PERCENT = old_chance
            emulator.SHOT_DISPERSION_SERVER_RADIUS_SCALE = old_scale
            emulator.SHOT_DISPERSION_CENTER_BIAS = old_bias

        self.assertTrue(fired)
        shot_pos = (0.0, 2.0, 0.0)
        target_pos = (0.0, 1.3, 100.0)
        visible = emulator.shot_dispersion_radius(shot_pos, target_pos)
        effective = visible * 0.4
        offset = abs(source["battle_last_shot_target_pos"][0])
        self.assertAlmostEqual(offset, effective)
        self.assertLess(offset, visible)

    def test_shot_dispersion_hit_chance_can_force_center_hit(self):
        source = _make_combat_session(505, 1, (0.0, 0.0, 40.0))
        shell = _make_shell(compact=9202, speed=500.0, gravity=9.81)
        source["battle_vehicle"].update({
            "defaultAmmo": [9202, 1],
            "shells": [shell],
        })
        source["battle_current_shell"] = 9202
        source["battle_ammo_stock"] = {9202: 1}
        source["battle_target_pos"] = (0.0, 1.3, 20.0)
        source["battle_target_pos_time"] = time.time()
        _set_gun_direction(source, (0.0, -0.7, -20.0))
        old_enabled = emulator.SHOT_DISPERSION_ENABLED
        old_chance = emulator.SHOT_HIT_CHANCE_PERCENT
        try:
            emulator.SHOT_DISPERSION_ENABLED = True
            emulator.SHOT_HIT_CHANCE_PERCENT = 100.0
            with mock.patch.object(emulator.random, "random", return_value=0.0), \
                    mock.patch.object(emulator.random, "uniform", return_value=0.0):
                _msgs, _reload_time, _shell_cd, fired = (
                    emulator.build_vehicle_shot_messages(source))
        finally:
            emulator.SHOT_DISPERSION_ENABLED = old_enabled
            emulator.SHOT_HIT_CHANCE_PERCENT = old_chance

        self.assertTrue(fired)
        self.assertAlmostEqual(source["battle_last_shot_target_pos"][0],
                               0.0)
        self.assertAlmostEqual(source["battle_last_shot_target_pos"][1],
                               1.3)
        self.assertAlmostEqual(source["battle_last_shot_target_pos"][2],
                               20.0)

    def test_damage_randomization_uses_configured_percent(self):
        old_randomization = emulator.SHOT_DAMAGE_RANDOMIZATION
        try:
            emulator.SHOT_DAMAGE_RANDOMIZATION = 0.25
            with mock.patch.object(emulator.random, "uniform", return_value=1.25):
                damage = emulator.get_shell_damage(
                    _make_shell(damage=100.0))
        finally:
            emulator.SHOT_DAMAGE_RANDOMIZATION = old_randomization

        self.assertEqual(damage, 125)

    def test_artillery_targeting_stores_grounded_marker(self):
        shell = _make_shell(kind="HIGH_EXPLOSIVE", compact=9300,
                            speed=120.0, gravity=160.0,
                            explosion_radius=10.0)
        artillery = _make_combat_vehicle()
        artillery.update({
            "vehicleClass": "SPG",
            "isSPG": True,
            "tags": frozenset(("SPG",)),
            "defaultAmmo": [9300, 1],
            "shells": [shell],
        })
        source = _make_combat_session(710, 1, (0.0, 0.0, 0.0),
                                      vehicle=artillery)

        with mock.patch.object(emulator, "sample_terrain",
                               return_value=(3.0, (0.0, 1.0, 0.0))):
            emulator.build_targeting_for_point(source, (4.0, 500.0, 20.0))

        self.assertEqual(source["battle_target_pos"], (4.0, 3.0, 20.0))

    def test_artillery_shot_uses_grounded_marker_target(self):
        shell = _make_shell(kind="HIGH_EXPLOSIVE", compact=9301,
                            speed=30.0, gravity=10.0,
                            explosion_radius=10.0)
        artillery = _make_combat_vehicle()
        artillery.update({
            "vehicleClass": "SPG",
            "isSPG": True,
            "tags": frozenset(("SPG",)),
            "defaultAmmo": [9301, 1],
            "shells": [shell],
        })
        source = _make_combat_session(711, 1, (0.0, 0.0, 0.0),
                                      vehicle=artillery)
        source["battle_current_shell"] = 9301
        source["battle_ammo_stock"] = {9301: 1}
        source["battle_target_pos"] = (0.0, 400.0, 20.0)
        source["battle_target_pos_time"] = time.time()
        _set_gun_direction(source, (
            0.0,
            math.sin(math.radians(45.0)),
            math.cos(math.radians(45.0)),
        ))
        with mock.patch.object(emulator, "sample_terrain",
                               return_value=(0.0, (0.0, 1.0, 0.0))), \
                mock.patch.object(emulator, "ray_static_obstacle_hit",
                                  return_value=None):
            _msgs, _reload_time, _shell_cd, fired = (
                emulator.build_vehicle_shot_messages(source))

        self.assertTrue(fired)
        impact = source["battle_last_shot_target_pos"]
        self.assertEqual(impact, (0.0, 0.0, 20.0))
        server_shot_vec = source["battle_last_server_shot_vec"]
        expected_vec = emulator.ballistic_shot_vec(
            (0.0, 2.0, 0.0), impact, shell["speed"],
            shell["gravity"], high_arc=True)
        self.assertAlmostEqual(server_shot_vec[0], expected_vec[0], places=5)
        self.assertAlmostEqual(server_shot_vec[1], expected_vec[1], places=5)
        self.assertAlmostEqual(server_shot_vec[2], expected_vec[2], places=5)

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

    def test_artillery_splash_blocked_by_static_obstacle_between_marker_and_tank(self):
        artillery = _make_combat_vehicle()
        artillery.update({
            "vehicleClass": "SPG",
            "isSPG": True,
            "tags": frozenset(("SPG",)),
        })
        source = _make_combat_session(701, 1, (0.0, 0.0, 40.0),
                                      vehicle=artillery)
        target = _make_combat_session(702, 2, (0.0, 0.0, 20.0), health=300)
        source["battle_arena_type_id"] = emulator.ARENA_TYPE_KARELIA
        shell = _make_shell(kind="HIGH_EXPLOSIVE", penetration=5.0,
                            damage=100.0, explosion_radius=10.0)
        source["battle_last_shot_shell"] = shell
        source["battle_last_shot_target_pos"] = (8.0, 1.3, 20.0)
        source["battle_last_shot_target_pos_time"] = time.time()
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[target["account_id"]] = target

        original_cache = dict(emulator.STATIC_OBSTACLE_CACHE)
        original_index = dict(emulator.STATIC_OBSTACLE_INDEX_CACHE)
        try:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_INDEX_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE[emulator.ARENA_TYPE_KARELIA] = [
                (4.0, 1.3, 20.0, 3.0, 4.0,
                 b"content/Environment/Rocks/blockingStone.model"),
            ]
            with mock.patch.object(emulator, "send_avatar_messages",
                                   return_value=True), \
                    mock.patch.object(emulator, "send_remote_vehicle",
                                      return_value=None):
                hit_target, damage, resolved = emulator.apply_shot_damage(
                    object(), source, shell, (0.0, 2.0, 40.0),
                    emulator.normalize_vec((0.2, -0.2, -1.0)))
        finally:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE.update(original_cache)
            emulator.STATIC_OBSTACLE_INDEX_CACHE.clear()
            emulator.STATIC_OBSTACLE_INDEX_CACHE.update(original_index)

        self.assertIsNone(hit_target)
        self.assertEqual(damage, 0)
        self.assertIsNone(resolved)
        self.assertEqual(target["battle_vehicle_health"], 300)

    def test_artillery_splash_passes_when_no_obstacle_between_marker_and_tank(self):
        artillery = _make_combat_vehicle()
        artillery.update({
            "vehicleClass": "SPG",
            "isSPG": True,
            "tags": frozenset(("SPG",)),
        })
        source = _make_combat_session(703, 1, (0.0, 0.0, 40.0),
                                      vehicle=artillery)
        target = _make_combat_session(704, 2, (0.0, 0.0, 20.0), health=300)
        source["battle_arena_type_id"] = emulator.ARENA_TYPE_KARELIA
        shell = _make_shell(kind="HIGH_EXPLOSIVE", penetration=5.0,
                            damage=100.0, explosion_radius=10.0)
        source["battle_last_shot_shell"] = shell
        source["battle_last_shot_target_pos"] = (4.0, 1.3, 20.0)
        source["battle_last_shot_target_pos_time"] = time.time()
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[target["account_id"]] = target

        original_cache = dict(emulator.STATIC_OBSTACLE_CACHE)
        original_index = dict(emulator.STATIC_OBSTACLE_INDEX_CACHE)
        try:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_INDEX_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE[emulator.ARENA_TYPE_KARELIA] = [
                (200.0, 1.3, 200.0, 3.0, 4.0,
                 b"content/Environment/Rocks/farStone.model"),
            ]
            with mock.patch.object(emulator, "send_avatar_messages",
                                   return_value=True), \
                    mock.patch.object(emulator, "send_remote_vehicle",
                                      return_value=None):
                hit_target, damage, resolved = emulator.apply_shot_damage(
                    object(), source, shell, (0.0, 2.0, 40.0),
                    emulator.normalize_vec((0.2, -0.2, -1.0)))
        finally:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE.update(original_cache)
            emulator.STATIC_OBSTACLE_INDEX_CACHE.clear()
            emulator.STATIC_OBSTACLE_INDEX_CACHE.update(original_index)

        self.assertIs(hit_target, target)
        self.assertGreater(damage, 0)
        self.assertTrue(resolved["artillerySplash"])

    def test_artillery_trace_impacts_static_obstacle_front_face(self):
        artillery = _make_combat_vehicle()
        shell = _make_shell(kind="HIGH_EXPLOSIVE", penetration=5.0,
                            damage=100.0, explosion_radius=10.0,
                            compact=9302, speed=100.0, gravity=0.0)
        artillery.update({
            "vehicleClass": "SPG",
            "isSPG": True,
            "tags": frozenset(("SPG",)),
            "defaultAmmo": [9302, 1],
            "shells": [shell],
        })
        source = _make_combat_session(705, 1, (0.0, 0.0, 0.0),
                                      vehicle=artillery)
        target = _make_combat_session(706, 2, (0.0, 0.0, 25.0), health=300)
        source["battle_arena_type_id"] = emulator.ARENA_TYPE_KARELIA
        source["battle_current_shell"] = 9302
        source["battle_ammo_stock"] = {9302: 1}
        source["battle_target_pos"] = (0.0, 1.3, 40.0)
        source["battle_target_pos_time"] = time.time()
        _set_gun_direction(source, (0.0, 0.0, 1.0))
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[target["account_id"]] = target

        original_cache = dict(emulator.STATIC_OBSTACLE_CACHE)
        original_index = dict(emulator.STATIC_OBSTACLE_INDEX_CACHE)
        try:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_INDEX_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE[emulator.ARENA_TYPE_KARELIA] = [
                (0.0, 2.0, 20.0, 4.0, 4.0,
                 b"content/Environment/Rocks/blockingStone.model"),
            ]
            with mock.patch.object(emulator, "sample_terrain",
                                   return_value=(0.0, (0.0, 1.0, 0.0))):
                _msgs, _reload_time, _shell_cd, fired = (
                    emulator.build_vehicle_shot_messages(source))
            impact = source["battle_last_shot_target_pos"]
            shot_info = source["battle_last_server_shot_info"]
            server_shot_vec = source["battle_last_server_shot_vec"]
            with mock.patch.object(emulator, "send_avatar_messages",
                                   return_value=True), \
                    mock.patch.object(emulator, "send_remote_vehicle",
                                      return_value=None):
                hit_target, damage, resolved = emulator.apply_shot_damage(
                    object(), source, shell, shot_info[1], server_shot_vec)
        finally:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE.update(original_cache)
            emulator.STATIC_OBSTACLE_INDEX_CACHE.clear()
            emulator.STATIC_OBSTACLE_INDEX_CACHE.update(original_index)

        self.assertTrue(fired)
        self.assertAlmostEqual(impact[2], 16.0, delta=0.25)
        self.assertLess(impact[2], 20.0)
        self.assertIsNone(hit_target)
        self.assertEqual(damage, 0)
        self.assertIsNone(resolved)
        self.assertEqual(target["battle_vehicle_health"], 300)

    def test_artillery_obstacle_block_disabled_by_config_flag(self):
        artillery = _make_combat_vehicle()
        artillery.update({
            "vehicleClass": "SPG",
            "isSPG": True,
            "tags": frozenset(("SPG",)),
        })
        source = _make_combat_session(707, 1, (0.0, 0.0, 40.0),
                                      vehicle=artillery)
        target = _make_combat_session(708, 2, (0.0, 0.0, 20.0), health=300)
        source["battle_arena_type_id"] = emulator.ARENA_TYPE_KARELIA
        shell = _make_shell(kind="HIGH_EXPLOSIVE", penetration=5.0,
                            damage=100.0, explosion_radius=10.0)
        source["battle_last_shot_shell"] = shell
        source["battle_last_shot_target_pos"] = (8.0, 1.3, 20.0)
        source["battle_last_shot_target_pos_time"] = time.time()
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[target["account_id"]] = target

        original_cache = dict(emulator.STATIC_OBSTACLE_CACHE)
        original_index = dict(emulator.STATIC_OBSTACLE_INDEX_CACHE)
        original_flag = emulator.ARTILLERY_OBSTACLE_BLOCKS_SHOT
        try:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_INDEX_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE[emulator.ARENA_TYPE_KARELIA] = [
                (4.0, 1.3, 20.0, 3.0, 4.0,
                 b"content/Environment/Rocks/blockingStone.model"),
            ]
            emulator.ARTILLERY_OBSTACLE_BLOCKS_SHOT = False
            with mock.patch.object(emulator, "send_avatar_messages",
                                   return_value=True), \
                    mock.patch.object(emulator, "send_remote_vehicle",
                                      return_value=None):
                hit_target, damage, resolved = emulator.apply_shot_damage(
                    object(), source, shell, (0.0, 2.0, 40.0),
                    emulator.normalize_vec((0.2, -0.2, -1.0)))
        finally:
            emulator.STATIC_OBSTACLE_CACHE.clear()
            emulator.STATIC_OBSTACLE_CACHE.update(original_cache)
            emulator.STATIC_OBSTACLE_INDEX_CACHE.clear()
            emulator.STATIC_OBSTACLE_INDEX_CACHE.update(original_index)
            emulator.ARTILLERY_OBSTACLE_BLOCKS_SHOT = original_flag

        self.assertIs(hit_target, target)
        self.assertGreater(damage, 0)
        self.assertTrue(resolved["artillerySplash"])

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
        _aim_gun_at(source, (4.0, 0.0, 20.0), shell, high_arc=True)
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

        with mock.patch.object(emulator, "sample_terrain",
                               return_value=(0.0, (0.0, 1.0, 0.0))), \
                mock.patch.object(emulator, "ray_static_obstacle_hit",
                                  return_value=None), \
                mock.patch.object(emulator, "send_avatar_messages", side_effect=capture_send), \
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
        marker = source["battle_last_shot_target_pos"]
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
        expected_delay = emulator.estimate_artillery_shell_flight_time(
            (source["battle_pos"][0], source["battle_pos"][1] + 2.0,
             source["battle_pos"][2]),
            marker,
            shell["speed"])
        self.assertGreaterEqual(scheduled[0][0], emulator.ARTILLERY_FLIGHT_TIME_MIN)
        self.assertAlmostEqual(scheduled[0][0], expected_delay)

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
        _aim_gun_at(source, (0.0, 0.0, 20.0), shell, high_arc=True)

        with mock.patch.object(emulator, "sample_terrain",
                               return_value=(0.0, (0.0, 1.0, 0.0))), \
                mock.patch.object(emulator, "ray_static_obstacle_hit",
                                  return_value=None):
            msgs, _reload_time, _shell_cd, fired = (
                emulator.build_vehicle_shot_messages(source))

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
        expected_time = emulator.estimate_artillery_shell_flight_time(
            (source["battle_pos"][0], source["battle_pos"][1] + 2.0,
             source["battle_pos"][2]),
            source["battle_last_shot_target_pos"],
            shell["speed"])
        self.assertEqual(source["battle_last_visual_flight_time"],
                         expected_time)

    def test_artillery_visible_tracer_time_scales_with_distance(self):
        shell = _make_shell(kind="HIGH_EXPLOSIVE", compact=9103,
                            speed=200.0, gravity=160.0)
        shot_pos = (0.0, 2.0, 0.0)
        near = (0.0, 1.3, 20.0)
        far = (0.0, 1.3, 400.0)

        near_time = emulator.build_artillery_visible_tracer(
            shot_pos, near, (0.0, -0.1, 1.0), shell)[4]
        far_time = emulator.build_artillery_visible_tracer(
            shot_pos, far, (0.0, -0.1, 1.0), shell)[4]

        self.assertAlmostEqual(near_time, emulator.ARTILLERY_FLIGHT_TIME_MIN)
        self.assertGreater(far_time, near_time)
        self.assertAlmostEqual(
            far_time,
            emulator.estimate_artillery_shell_flight_time(
                shot_pos, far, shell["speed"]))

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

    def test_direct_shot_uses_current_barrel_direction_when_marker_is_sideways(self):
        shell = _make_shell(compact=9104, speed=500.0, gravity=9.81)
        tank = _make_combat_vehicle()
        tank.update({
            "defaultAmmo": [9104, 1],
            "shells": [shell],
        })
        source = _make_combat_session(175, 1, (0.0, 0.0, 0.0),
                                      vehicle=tank)
        target = _make_combat_session(176, 2, (8.0, 0.0, 0.0), health=300)
        source["battle_current_shell"] = 9104
        source["battle_ammo_stock"] = {9104: 1}
        source["battle_turret_yaw"] = 0.0
        source["battle_gun_pitch"] = 0.0
        source["battle_target_pos"] = (8.0, 1.3, 0.0)
        source["battle_target_pos_time"] = time.time()
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[target["account_id"]] = target

        _msgs, _reload_time, _shell_cd, fired = (
            emulator.build_vehicle_shot_messages(source))
        shot_vec = source["battle_last_server_shot_vec"]
        marker = source["battle_last_shot_target_pos"]

        self.assertTrue(fired)
        self.assertAlmostEqual(shot_vec[0], 0.0, places=5)
        self.assertAlmostEqual(shot_vec[1], 0.0, places=5)
        self.assertAlmostEqual(shot_vec[2], 1.0, places=5)
        self.assertAlmostEqual(marker[0], 0.0, places=5)
        self.assertGreater(marker[2], 0.0)

        with mock.patch.object(emulator, "ray_static_obstacle_hit",
                               return_value=None):
            hit_target, damage, resolved = emulator.apply_shot_damage(
                object(), source, shell, (0.0, 2.0, 0.0), shot_vec)

        self.assertIsNone(hit_target)
        self.assertEqual(damage, 0)
        self.assertIsNone(resolved)
        self.assertEqual(target["battle_vehicle_health"], 300)

    def test_direct_shot_uses_marker_when_gun_is_nearly_aligned(self):
        shell = _make_shell(compact=9105, speed=500.0, gravity=9.81)
        tank = _make_combat_vehicle()
        tank.update({
            "defaultAmmo": [9105, 1],
            "shells": [shell],
        })
        source = _make_combat_session(177, 1, (0.0, 0.0, 0.0),
                                      vehicle=tank)
        source["battle_current_shell"] = 9105
        source["battle_ammo_stock"] = {9105: 1}
        source["battle_turret_yaw"] = math.radians(20.0)
        source["battle_gun_pitch"] = 0.0
        source["battle_target_pos"] = (0.0, 2.0, 100.0)
        source["battle_target_pos_time"] = time.time()

        _msgs, _reload_time, _shell_cd, fired = (
            emulator.build_vehicle_shot_messages(source))
        shot_vec = source["battle_last_server_shot_vec"]
        marker = source["battle_last_shot_target_pos"]

        self.assertTrue(fired)
        self.assertAlmostEqual(shot_vec[0], 0.0, places=5)
        self.assertAlmostEqual(shot_vec[1], 0.0, places=5)
        self.assertAlmostEqual(shot_vec[2], 1.0, places=5)
        self.assertAlmostEqual(marker[0], 0.0, places=5)
        self.assertAlmostEqual(marker[2], 100.0, places=5)

    def test_direct_marker_vertical_slop_accepts_client_marker_above_tank(self):
        source = _make_combat_session(178, 1, (0.0, 0.0, 40.0))
        target = _make_combat_session(179, 2, (0.0, 15.0, 0.0), health=300)
        marker = (6.85, 20.86, 0.0)
        source["battle_last_shot_target_pos"] = marker
        source["battle_last_shot_target_pos_time"] = time.time()
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[target["account_id"]] = target
        shot_pos = (0.0, 17.0, 40.0)
        shot_vec = emulator.normalize_vec((
            marker[0] - shot_pos[0],
            marker[1] - shot_pos[1],
            marker[2] - shot_pos[2],
        ))

        with mock.patch.object(emulator, "ray_static_obstacle_hit",
                               return_value=None):
            hit_info = emulator.find_shot_target(source, shot_pos, shot_vec)

        self.assertIsNotNone(hit_info)
        self.assertIs(hit_info["target"], target)

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

    def test_unknown_remote_shot_delays_visuals_after_intro(self):
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
        self.assertEqual(sent[0][1], b"INTRO")
        self.assertIn(source["account_id"], observer["known_remote_accounts"])
        self.assertIn(source["account_id"], observer["arena_remote_accounts"])
        self.assertEqual(len(scheduled), 1)
        self.assertAlmostEqual(scheduled[0][0],
                               emulator.REMOTE_ENTITY_INTRO_GRACE_SECONDS,
                               places=2)

        before_callback = len(sent)
        observer["remote_vehicle_intro_times"][source["account_id"]] = (
            time.time() - emulator.REMOTE_ENTITY_INTRO_GRACE_SECONDS - 0.01)
        with mock.patch.object(emulator, "send_avatar_messages", side_effect=capture_send):
            scheduled[0][1]()
        self.assertEqual(len(sent), before_callback + 1)
        self.assertEqual(_message_ids(sent[-1][1]),
                         [emulator.VEHICLE_SHOW_SHOOTING_MSG_ID,
                          emulator.AVATAR_SHOW_TRACER_MSG_ID])

    def test_fresh_known_remote_shot_waits_for_intro_grace(self):
        source = _make_combat_session(185, 1, (0.0, 0.0, 40.0))
        observer = _make_combat_session(186, 2, (0.0, 0.0, 20.0))
        observer["known_remote_accounts"].add(source["account_id"])
        emulator.mark_remote_vehicle_intro_sent(observer, source)
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

        with mock.patch.object(emulator, "send_avatar_messages", side_effect=capture_send), \
                mock.patch.object(emulator, "runtime_call_later", side_effect=capture_later):
            emulator.broadcast_remote_vehicle_shot(
                object(), source, 124.0, (0.0, 2.0, 40.0),
                (0.0, 0.0, -100.0), 9.81, 0)

        self.assertEqual(sent, [])
        self.assertEqual(len(scheduled), 1)
        self.assertGreater(scheduled[0][0], 0.0)

        observer["remote_vehicle_intro_times"][source["account_id"]] = (
            time.time() - emulator.REMOTE_ENTITY_INTRO_GRACE_SECONDS - 0.01)
        with mock.patch.object(emulator, "send_avatar_messages", side_effect=capture_send):
            scheduled[0][1]()
        self.assertEqual(len(sent), 1)
        self.assertEqual(_message_ids(sent[0][1]),
                         [emulator.VEHICLE_SHOW_SHOOTING_MSG_ID,
                          emulator.AVATAR_SHOW_TRACER_MSG_ID])

    def test_firing_can_reduce_camo_and_reveal_shooter(self):
        source = _make_combat_session(
            187, 2, (0.0, 0.0, 300.0),
            vehicle={
                "vehicleClass": "lightTank",
                "invisibilityMoving": 0.9,
                "invisibilityStill": 0.9,
                "gunInvisibilityFactorAtShot": 0.1,
            })
        observer = _make_combat_session(
            188, 1, (0.0, 0.0, 0.0),
            vehicle={
                "vehicleClass": "lightTank",
                "circularVisionRadius": 360.0,
            })
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

        with mock.patch.object(emulator, "iter_spotting_bushes_near_segment",
                               side_effect=lambda *args, **kwargs: iter(())):
            self.assertFalse(emulator.is_vehicle_visible_to(observer, source))
            with mock.patch.object(emulator, "build_remote_vehicle_messages",
                                   return_value=b"INTRO"), \
                    mock.patch.object(emulator, "send_avatar_messages",
                                      side_effect=capture_send), \
                    mock.patch.object(emulator, "runtime_call_later",
                                      side_effect=capture_later):
                emulator.broadcast_remote_vehicle_shot(
                    object(), source, 321.0, (0.0, 2.0, 300.0),
                    (0.0, 0.0, -100.0), 9.81, 0)

        self.assertEqual(len(sent), 1)
        self.assertIs(sent[0][0], observer)
        self.assertTrue(sent[0][1].startswith(b"INTRO"))
        self.assertIn(source["account_id"], observer["known_remote_accounts"])
        self.assertIn(source["account_id"], observer["arena_remote_accounts"])
        self.assertEqual(len(scheduled), 1)

    def test_hidden_remote_shot_does_not_send_direct_tracer_or_intro(self):
        source = _make_combat_session(
            189, 2, (0.0, 0.0, 300.0),
            vehicle={
                "vehicleClass": "lightTank",
                "invisibilityMoving": 0.95,
                "invisibilityStill": 0.95,
                "gunInvisibilityFactorAtShot": 1.0,
            })
        observer = _make_combat_session(
            190, 1, (0.0, 0.0, 0.0),
            vehicle={
                "vehicleClass": "heavyTank",
                "circularVisionRadius": 300.0,
            })
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[observer["account_id"]] = observer
        sent = []

        def capture_send(_sock, _addr, _sess, msgs, _label, reliable=True):
            sent.append((_sess, bytes(msgs), reliable))
            return True

        with mock.patch.object(emulator, "iter_spotting_bushes_near_segment",
                               side_effect=lambda *args, **kwargs: iter(())), \
                mock.patch.object(emulator, "build_remote_vehicle_messages",
                                  return_value=b"INTRO") as build_intro, \
                mock.patch.object(emulator, "send_avatar_messages",
                                  side_effect=capture_send):
            emulator.broadcast_remote_vehicle_shot(
                object(), source, 654.0, (0.0, 2.0, 300.0),
                (0.0, 0.0, -100.0), 9.81, 0)

        self.assertEqual(sent, [])
        self.assertNotIn(source["account_id"], observer["known_remote_accounts"])
        build_intro.assert_not_called()

    def test_recent_shot_keeps_known_remote_from_leaving_aoi_immediately(self):
        source = _make_combat_session(
            191, 2, (0.0, 0.0, 1000.0),
            vehicle={"vehicleClass": "lightTank"})
        observer = _make_combat_session(
            192, 1, (0.0, 0.0, 0.0),
            vehicle={"vehicleClass": "heavyTank", "circularVisionRadius": 50.0})
        observer["known_remote_accounts"].add(source["account_id"])
        source["battle_last_shot_time"] = time.time()
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[observer["account_id"]] = observer
        sent = []

        def capture_send(_sock, _addr, _sess, msgs, _label, reliable=True):
            sent.append(bytes(msgs))
            return True

        with mock.patch.object(emulator, "send_avatar_messages",
                               side_effect=capture_send):
            visible = emulator.update_remote_vehicle_visibility(
                object(), observer, source)

        self.assertFalse(visible)
        self.assertIn(source["account_id"], observer["known_remote_accounts"])
        self.assertEqual(sent, [])

    def test_visibility_loss_sends_leave_aoi_after_shot_grace(self):
        source = _make_combat_session(
            197, 2, (0.0, 0.0, 1000.0),
            vehicle={"vehicleClass": "lightTank"})
        observer = _make_combat_session(
            198, 1, (0.0, 0.0, 0.0),
            vehicle={"vehicleClass": "heavyTank", "circularVisionRadius": 50.0})
        observer["known_remote_accounts"].add(source["account_id"])
        source["battle_last_shot_time"] = (
            time.time() - emulator.SHOT_VISIBILITY_GRACE_SECONDS - 1.0)
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[observer["account_id"]] = observer
        sent = []

        def capture_send(_sock, _addr, _sess, msgs, _label, reliable=True):
            sent.append(bytes(msgs))
            return True

        with mock.patch.object(emulator, "send_avatar_messages",
                               side_effect=capture_send):
            visible = emulator.update_remote_vehicle_visibility(
                object(), observer, source)

        self.assertFalse(visible)
        self.assertNotIn(source["account_id"], observer["known_remote_accounts"])
        self.assertTrue(sent)
        self.assertIn(emulator.CLIENT_LEAVE_AOI_MSG_ID, _message_ids(sent[0]))

    def test_fresh_intro_visibility_loss_keeps_remote_in_aoi(self):
        source = _make_combat_session(
            199, 2, (0.0, 0.0, 1000.0),
            vehicle={"vehicleClass": "lightTank"})
        observer = _make_combat_session(
            200, 1, (0.0, 0.0, 0.0),
            vehicle={"vehicleClass": "heavyTank", "circularVisionRadius": 50.0})
        observer["known_remote_accounts"].add(source["account_id"])
        emulator.mark_remote_vehicle_intro_sent(observer, source)
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[observer["account_id"]] = observer
        sent = []

        def capture_send(_sock, _addr, _sess, msgs, _label, reliable=True):
            sent.append(bytes(msgs))
            return True

        with mock.patch.object(emulator, "send_avatar_messages",
                               side_effect=capture_send):
            visible = emulator.update_remote_vehicle_visibility(
                object(), observer, source)

        self.assertFalse(visible)
        self.assertIn(source["account_id"], observer["known_remote_accounts"])
        self.assertEqual(sent, [])

    def test_known_hidden_remote_shot_does_not_leave_aoi_or_send_tracer(self):
        source = _make_combat_session(
            195, 2, (0.0, 0.0, 300.0),
            vehicle={
                "vehicleClass": "lightTank",
                "invisibilityMoving": 0.95,
                "invisibilityStill": 0.95,
                "gunInvisibilityFactorAtShot": 1.0,
            })
        observer = _make_combat_session(
            196, 1, (0.0, 0.0, 0.0),
            vehicle={
                "vehicleClass": "heavyTank",
                "circularVisionRadius": 300.0,
            })
        observer["known_remote_accounts"].add(source["account_id"])
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[observer["account_id"]] = observer
        sent = []

        def capture_send(_sock, _addr, _sess, msgs, _label, reliable=True):
            sent.append(bytes(msgs))
            return True

        with mock.patch.object(emulator, "iter_spotting_bushes_near_segment",
                               side_effect=lambda *args, **kwargs: iter(())), \
                mock.patch.object(emulator, "send_avatar_messages",
                                  side_effect=capture_send):
            emulator.broadcast_remote_vehicle_shot(
                object(), source, 655.0, (0.0, 2.0, 300.0),
                (0.0, 0.0, -100.0), 9.81, 0)

        self.assertIn(source["account_id"], observer["known_remote_accounts"])
        self.assertEqual(sent, [])

    def test_hidden_direct_shot_still_applies_damage_without_target_tracer(self):
        shell = _make_shell(compact=9303, penetration=500.0,
                            damage=100.0, speed=500.0, gravity=9.81)
        source_vehicle = _make_combat_vehicle()
        source_vehicle.update({
            "vehicleClass": "lightTank",
            "invisibilityMoving": 0.95,
            "invisibilityStill": 0.95,
            "gunInvisibilityFactorAtShot": 1.0,
            "defaultAmmo": [9303, 1],
            "shells": [shell],
            "reloadTime": 5.0,
        })
        target_vehicle = _make_combat_vehicle(health=300)
        target_vehicle.update({
            "vehicleClass": "heavyTank",
            "circularVisionRadius": 50.0,
        })
        source = _make_combat_session(193, 2, (0.0, 0.0, 300.0),
                                      vehicle=source_vehicle)
        target = _make_combat_session(194, 1, (0.0, 0.0, 20.0),
                                      health=300, vehicle=target_vehicle)
        source["battle_current_shell"] = 9303
        source["battle_ammo_stock"] = {9303: 1}
        source["battle_target_pos"] = (0.0, 1.3, 20.0)
        source["battle_target_pos_time"] = time.time()
        _set_gun_direction(source, (0.0, -0.7, -280.0))
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[target["account_id"]] = target
        sent = []

        def capture_send(_sock, _addr, _sess, msgs, _label, reliable=True):
            sent.append((_sess, bytes(msgs), reliable))
            return True

        with mock.patch.object(emulator, "iter_spotting_bushes_near_segment",
                               side_effect=lambda *args, **kwargs: iter(())), \
                mock.patch.object(emulator, "ray_static_obstacle_hit",
                                  return_value=None), \
                mock.patch.object(emulator, "send_remote_vehicle",
                                  return_value=None), \
                mock.patch.object(emulator, "send_avatar_messages",
                                  side_effect=capture_send), \
                mock.patch.object(emulator, "runtime_call_later",
                                  return_value=None):
            self.assertFalse(emulator.is_vehicle_visible_to(target, source))
            emulator.handle_vehicle_shot(object(), source["addr"], source)

        self.assertEqual(target["battle_vehicle_health"], 200)
        target_messages = b"".join(
            msg for sess, msg, _reliable in sent if sess is target)
        target_ids = _message_ids(target_messages)
        self.assertNotIn(emulator.AVATAR_SHOW_TRACER_MSG_ID, target_ids)
        self.assertNotIn(emulator.AVATAR_STOP_TRACER_MSG_ID, target_ids)
        self.assertNotIn(emulator.AVATAR_EXPLODE_PROJECTILE_MSG_ID,
                         target_ids)
        damage_payloads = _message_payloads(
            target_messages,
            emulator.VEHICLE_SHOW_DAMAGE_FROM_SHOT_MSG_ID)
        self.assertFalse(damage_payloads)
        health_payloads = [
            payload for payload in _message_payloads(
                target_messages,
                emulator.AVATAR_UPDATE_VEHICLE_HEALTH_MSG_ID)
            if len(payload) == 7
        ]
        self.assertTrue(health_payloads)
        explosion_payloads = [
            payload for payload in _message_payloads(
                target_messages,
                emulator.VEHICLE_SHOW_DAMAGE_FROM_EXPLOSION_MSG_ID)
            if len(payload) == 21
        ]
        self.assertFalse(explosion_payloads)

    def test_known_target_hit_feedback_survives_visibility_flicker(self):
        shell = _make_shell(compact=9304, penetration=500.0,
                            damage=100.0, speed=500.0, gravity=9.81)
        source_vehicle = _make_combat_vehicle()
        source_vehicle.update({
            "vehicleClass": "heavyTank",
            "circularVisionRadius": 50.0,
            "defaultAmmo": [9304, 1],
            "shells": [shell],
        })
        target_vehicle = _make_combat_vehicle(health=300)
        target_vehicle.update({
            "vehicleClass": "heavyTank",
            "circularVisionRadius": 50.0,
        })
        source = _make_combat_session(197, 1, (0.0, 0.0, 0.0),
                                      vehicle=source_vehicle)
        target = _make_combat_session(198, 2, (0.0, 0.0, 300.0),
                                      health=300, vehicle=target_vehicle)
        source["known_remote_accounts"].add(target["account_id"])
        source["arena_remote_accounts"] = {target["account_id"]}
        source["battle_last_shot_target_pos"] = (0.0, 1.3, 300.0)
        source["battle_last_shot_target_pos_time"] = time.time()
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[target["account_id"]] = target
        sent = []

        def capture_send(_sock, _addr, _sess, msgs, _label, reliable=True):
            sent.append((_sess, bytes(msgs), reliable))
            return True

        with mock.patch.object(emulator, "iter_spotting_bushes_near_segment",
                               side_effect=lambda *args, **kwargs: iter(())), \
                mock.patch.object(emulator, "ray_static_obstacle_hit",
                                  return_value=None), \
                mock.patch.object(emulator, "send_avatar_messages",
                                  side_effect=capture_send):
            self.assertFalse(emulator.is_vehicle_visible_to(source, target))
            hit_target, damage, resolved = emulator.apply_shot_damage(
                object(), source, shell,
                (0.0, 2.0, 0.0),
                emulator.normalize_vec((0.0, -0.7, 300.0)))

        self.assertIs(hit_target, target)
        self.assertGreater(damage, 0)
        self.assertIsNotNone(resolved)
        self.assertLess(target["battle_vehicle_health"], 300)
        self.assertIn(target["account_id"], source["known_remote_accounts"])
        source_messages = b"".join(
            msg for sess, msg, _reliable in sent if sess is source)
        health_payloads = _message_payloads(
            source_messages,
            0x09)
        remote_id = emulator.get_remote_vehicle_id(target)
        remote_health = [
            struct.unpack_from("<h", payload, 6)[0]
            for payload in health_payloads
            if len(payload) == 10 and
            struct.unpack_from("<I", payload, 0)[0] == remote_id
        ]

        self.assertEqual(remote_health[-1], target["battle_vehicle_health"])

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
        joined = b"".join(sent_messages)
        self.assertFalse(_message_payloads(
            joined, emulator.VEHICLE_SHOW_DAMAGE_FROM_SHOT_MSG_ID))
        self.assertFalse([
            payload for payload in _message_payloads(
                joined, emulator.VEHICLE_SHOW_DAMAGE_FROM_EXPLOSION_MSG_ID)
            if len(payload) == 21
        ])

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

    def test_direct_ray_hit_without_fresh_marker_damages_close_target(self):
        source = _make_combat_session(186, 1, (0.0, 0.0, 0.0))
        target = _make_combat_session(187, 2, (0.0, 0.0, 5.0), health=300)
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[target["account_id"]] = target

        with mock.patch.object(emulator, "ray_static_obstacle_hit",
                               return_value=None), \
                mock.patch.object(emulator, "send_avatar_messages",
                                  return_value=True), \
                mock.patch.object(emulator, "send_remote_vehicle",
                                  return_value=None):
            hit_target, damage, resolved = emulator.apply_shot_damage(
                object(), source, _make_shell(penetration=200.0, damage=100.0),
                (0.0, 1.3, 0.0), (0.0, 0.0, 1.0))

        self.assertIs(hit_target, target)
        self.assertEqual(damage, 100)
        self.assertEqual(target["battle_vehicle_health"], 200)
        self.assertEqual(resolved["result"], emulator.SHOT_RESULT_ARMOR_PIERCED)

    def test_direct_ray_hit_prefers_close_tank_before_marker_target(self):
        source = _make_combat_session(188, 1, (0.0, 0.0, 0.0))
        close = _make_combat_session(189, 2, (0.0, 0.0, 20.0), health=300)
        far = _make_combat_session(190, 2, (0.0, 0.0, 80.0), health=300)
        source["battle_last_shot_target_pos"] = (0.0, 1.3, 80.0)
        source["battle_last_shot_target_pos_time"] = time.time()
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[close["account_id"]] = close
        emulator.active_battle_accounts[far["account_id"]] = far

        with mock.patch.object(emulator, "ray_static_obstacle_hit",
                               return_value=None), \
                mock.patch.object(emulator, "send_avatar_messages",
                                  return_value=True), \
                mock.patch.object(emulator, "send_remote_vehicle",
                                  return_value=None):
            hit_target, damage, resolved = emulator.apply_shot_damage(
                object(), source, _make_shell(penetration=500.0, damage=100.0),
                (0.0, 1.3, 0.0), (0.0, 0.0, 1.0))

        self.assertIs(hit_target, close)
        self.assertEqual(damage, 100)
        self.assertEqual(close["battle_vehicle_health"], 200)
        self.assertEqual(far["battle_vehicle_health"], 300)
        self.assertEqual(resolved["result"], emulator.SHOT_RESULT_ARMOR_PIERCED)

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

    def test_killed_vehicle_freezes_without_battle_end(self):
        source = _make_combat_session(153, 1, (0.0, 0.0, 0.0))
        target = _make_combat_session(154, 2, (0.0, 0.0, 20.0), health=80)
        teammate = _make_combat_session(155, 2, (20.0, 0.0, 20.0), health=300)
        target["battle_motion_flags"] = 1
        target["battle_speed"] = 7.0
        target["battle_rspeed"] = 1.0
        target["battle_client_control_enabled"] = True
        target["server_vehicle_authoritative"] = False
        target["client_vehicle_pos"] = (0.0, 0.0, 22.0)
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
        emulator.active_battle_accounts[teammate["account_id"]] = teammate

        with mock.patch.object(emulator, "find_shot_target", return_value=hit_info), \
                mock.patch.object(emulator, "send_avatar_messages", return_value=True), \
                mock.patch.object(emulator, "send_remote_vehicle", return_value=None):
            _target, damage, _resolved = emulator.apply_shot_damage(
                object(), source, _make_shell(penetration=200.0, damage=100.0),
                (0.0, 0.0, 0.0), (0.0, 0.0, -1.0))

        self.assertEqual(damage, 80)
        self.assertEqual(target["battle_vehicle_health"], 0)
        self.assertFalse(source.get("battle_ended", False))
        self.assertEqual(target["battle_motion_flags"], 0)
        self.assertEqual(target["battle_speed"], 0.0)
        self.assertEqual(target["battle_rspeed"], 0.0)
        self.assertFalse(target["battle_client_control_enabled"])
        self.assertTrue(target["server_vehicle_authoritative"])
        self.assertIsNone(target["client_vehicle_pos"])

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

    def test_direct_client_damage_effects_uses_shot_message(self):
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
        self.assertIn(bytes([emulator.VEHICLE_SHOW_DAMAGE_FROM_SHOT_MSG_ID]),
                      msg_ids)
        self.assertNotIn(bytes([emulator.VEHICLE_SHOW_DAMAGE_FROM_EXPLOSION_MSG_ID]),
                         msg_ids)

    def test_unspotted_direct_shot_does_not_send_anonymous_tracer(self):
        source = _make_combat_session(201, 1, (0.0, 0.0, 0.0))
        observer = _make_combat_session(202, 2, (1000.0, 0.0, 0.0))
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[observer["account_id"]] = observer
        sent_messages = []

        def capture_send(_sock, _addr, _sess, msgs, _label, reliable=True):
            sent_messages.append(bytes(msgs))
            return True

        with mock.patch.object(emulator, "send_avatar_messages", side_effect=capture_send), \
                mock.patch.object(emulator, "hide_remote_vehicle", return_value=False):
            emulator.broadcast_remote_vehicle_shot(
                object(), source, 777, (0.0, 2.0, 0.0),
                (0.0, 0.0, 800.0), 9.81, 0)

        self.assertEqual(sent_messages, [])
        self.assertEqual(source.get("battle_visual_shot_viewers"), {777: set()})

    def test_projectile_impact_only_goes_to_tracer_viewers(self):
        source = _make_combat_session(211, 1, (0.0, 0.0, 0.0))
        hidden = _make_combat_session(212, 2, (1000.0, 0.0, 0.0))
        emulator.active_battle_accounts[source["account_id"]] = source
        emulator.active_battle_accounts[hidden["account_id"]] = hidden
        source["battle_visual_shot_viewers"] = {888: {source["account_id"]}}
        sent_sessions = []

        def capture_send(_sock, _addr, sess, msgs, _label, reliable=True):
            sent_sessions.append(sess)
            return True

        with mock.patch.object(emulator, "send_avatar_messages", side_effect=capture_send):
            emulator.broadcast_projectile_impact(
                object(), source, 888, 0, (0.0, 0.0, 100.0),
                (0.0, 0.0, 1.0))

        self.assertEqual(sent_sessions, [source])
        self.assertEqual(source.get("battle_visual_shot_viewers"), {})

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

    def test_ap_direct_hit_uses_damage_from_shot_message(self):
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
            bytes([emulator.VEHICLE_SHOW_DAMAGE_FROM_SHOT_MSG_ID]),
            msg_ids)
        self.assertNotIn(
            bytes([emulator.VEHICLE_SHOW_DAMAGE_FROM_EXPLOSION_MSG_ID]),
            msg_ids)

    def test_he_direct_hit_uses_damage_from_shot_message(self):
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
            bytes([emulator.VEHICLE_SHOW_DAMAGE_FROM_SHOT_MSG_ID]),
            msg_ids)
        self.assertNotIn(
            bytes([emulator.VEHICLE_SHOW_DAMAGE_FROM_EXPLOSION_MSG_ID]),
            msg_ids)


if __name__ == "__main__":
    unittest.main()
