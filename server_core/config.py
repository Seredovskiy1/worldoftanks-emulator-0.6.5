import copy
import glob
import json
import os


DEFAULT_CONFIG = {
    "server": {
        "login_port": 20016,
        "baseapp_port": 20017,
        "public_host": "26.108.162.225",
        "private_key_path": "private_key.pem"
    },
    "database": {
        "host": "127.0.0.1",
        "port": 3306,
        "user": "root",
        "password": "",
        "name": "wot_emulator"
    },
    "account": {
        "default_credits": 1000000000,
        "default_gold": 1000000,
        "default_free_xp": 1000000,
        "default_slots": 200,
        "default_berths": 50,
        "unlock_all_vehicles": False
    },
    "data": {
        "vehicles_path": "data/_vehicles.json",
        "tankmen_path": "data/_tankmen.json",
        "artefacts_path": "data/_artefacts.json",
        "max_vehicles_inline": None
    },
    "matchmaking": {
        "min_seconds": 15,
        "max_seconds": 20,
        "team_balance_mode": "health_weight",
        "team_weight_field": "maxHealth"
    },
    "battle": {
        "prebattle_timer_seconds": 25,
        "battle_timer_seconds": 900,
        "ready_guard_seconds": 1.0,
        "auto_ready_fallback_seconds": 6.0,
        "own_vehicle_spawn_delay_seconds": 4.0,
        "period_time_offset_seconds": 3.0,
        "tick_hz": 60.0,
        "verbose_debug": False,
        "targeting_update_interval": 0.08,
        "base_capture_radius": 50.0,
        "base_capture_points_max": 100,
        "base_capture_points_per_second": 1.0,
        "base_capture_max_points_per_second": 3.0,
        "base_capture_client_position_max_age": 5.0
    },
    "combat": {
        "client_shot_damage_effects": False,
        "vehicle_speed_multiplier": 1.0,
        "vehicle_rotation_multiplier": 1.5,
        "vehicle_max_health_overrides": {
            "Lowe": 1650
        },
        "default_forward_speed": 10.0,
        "default_backward_speed": 4.0,
        "default_rotation_speed_degrees": 30.0,
        "default_accel": 1.8,
        "default_decel": 6.0,
        "default_rot_accel": 2.0,
        "default_rot_decel": 3.0,
        "rotation_speed_factor": 1.8,
        "rotation_accel_factor_xml": 2.0,
        "rotation_boost_full_weight_t": 35.0,
        "rotation_boost_none_weight_t": 55.0,
        "light_rot_accel_weight_t": 18.0,
        "light_rot_accel_hp_per_ton": 16.0,
        "light_rot_accel_bonus": 1.45,
        "accel_hp_per_ton_factor": 0.18,
        "accel_min": 0.6,
        "accel_max": 8.0,
        "accel_light_top_speed_time": 4.0,
        "accel_medium_top_speed_time": 4.8,
        "accel_heavy_top_speed_time": 5.5,
        "speed_decel_ratio": 3.5,
        "decel_min": 5.0,
        "decel_max": 24.0,
        "coast_decel_ratio": 0.55,
        "coast_decel_min": 2.2,
        "coast_decel_max": 4.5,
        "rot_accel_factor": 2.0,
        "rot_accel_min": 1.0,
        "rot_decel_ratio": 1.5,
        "rot_decel_min": 1.5,
        "min_turn_factor": 1.0,
        "heavy_min_turn_factor": 0.7,
        "remote_gun_pitch_scale": 0.35,
        "remote_gun_pitch_limit_degrees": 12.0,
        "shot_trace_distance": 1200.0,
        "target_marker_occlusion_max_age": 3.0,
        "shot_dispersion_enabled": True,
        "shot_hit_chance_percent": 70.0,
        "shot_dispersion_radius_at_100m": 2.5,
        "shot_dispersion_server_radius_scale": 0.45,
        "shot_dispersion_center_bias": 2.5,
        "shot_dispersion_min_radius": 0.25,
        "shot_dispersion_max_radius": 30.0,
        "shot_damage_randomization": 0.25,
        "shot_ap_nonpen_damage_factor": 0.0,
        "shot_ricochet_damage_factor": 0.0,
        "shot_penetration_factor": 1.0,
        "spotting_enabled": True,
        "spotting_auto_reveal_distance": 50.0,
        "spotting_max_range": 445.0,
        "spotting_stationary_speed": 0.5,
        "spotting_shot_camo_penalty_seconds": 5.0,
        "shot_visibility_grace_seconds": 1.0,
        "keep_remote_entities_on_visibility_loss": False,
        "spotting_camo_scale": 2.0,
        "spotting_camo_class_multipliers": {
            "lightTank": 1.35,
            "mediumTank": 1.0,
            "AT-SPG": 1.05,
            "heavyTank": 0.8,
            "SPG": 0.85
        },
        "spotting_view_range_fallbacks": {
            "lightTank": 360.0,
            "mediumTank": 330.0,
            "AT-SPG": 330.0,
            "heavyTank": 300.0,
            "SPG": 300.0
        },
        "spotting_view_range_class_limits": {
            "lightTank": [410.0, 445.0],
            "mediumTank": [350.0, 390.0],
            "AT-SPG": [340.0, 380.0],
            "heavyTank": [300.0, 340.0],
            "SPG": [280.0, 320.0]
        },
        "spotting_camo_fallbacks": {
            "lightTank": [0.18, 0.22],
            "mediumTank": [0.12, 0.17],
            "AT-SPG": [0.10, 0.18],
            "heavyTank": [0.05, 0.10],
            "SPG": [0.04, 0.08]
        },
        "spotting_bush_radius": 7.5,
        "spotting_bush_bonus": 0.08,
        "spotting_bush_max_bonus": 0.25,
        "spotting_bush_patterns": [
            "bush",
            "scrub",
            "juniper",
            "shrub",
            "pampas"
        ],
        "client_position_max_age": 0.75,
        "client_avatar_vehicle_pos_max_delta": 80.0,
        "server_vehicle_authoritative": True,
        "client_authoritative_vehicle_control": False,
        "own_vehicle_sync_interval": 0.0,
        "shot_tank_center_height": 1.3,
        "shot_tank_half_length": 5.2,
        "shot_tank_half_width": 2.4,
        "shot_tank_min_height": 0.15,
        "shot_tank_max_height": 3.8,
        "shot_tank_hit_radius_h": 7.0,
        "shot_tank_hit_radius_v": 6.0,
        "shot_gun_alignment_tolerance_degrees": 35.0,
        "shot_tank_marker_vert_above": 25.0,
        "shot_armor_min_cos": 0.12,
        "shot_armor_autoricochet_degrees": 70.0,
        "shot_penetration_near_distance": 100.0,
        "shot_penetration_far_distance": 500.0,
        "shot_he_nonpen_damage_factor": 0.18,
        "shot_he_min_splash_damage": 1,
        "artillery_splash_min_factor": 0.35,
        "artillery_direct_he_damage_factor": 0.55,
        "artillery_splash_radius_factor": 2.0,
        "artillery_splash_caliber_factor": 15.0,
        "artillery_splash_max_radius": 18.0,
        "artillery_direct_hit_radius_h": 3.4,
        "artillery_direct_hit_radius_v": 5.5,
        "artillery_flight_time_min": 0.45,
        "artillery_flight_time_max": 4.5,
        "artillery_visible_tracer_time": 1.6,
        "artillery_visible_tracer_back_distance": 70.0,
        "artillery_visible_tracer_height": 80.0,
        "artillery_visible_tracer_gravity_factor": 0.15,
        "artillery_visible_tracer_min_vx": 8.0,
        "direct_projectile_impact_min_delay": 0.08,
        "direct_projectile_impact_max_delay": 0.35,
        "remote_shot_intro_delay": 0.05,
        "remote_entity_intro_grace_seconds": 0.5,
        "remote_shot_sound_delay": 0.25,
        "target_hit_radius": 8.0,
        "target_aim_radius": 8.0,
        "shot_target_overshoot": 10.0,
        "static_obstacle_move_radius_factor": 0.5,
        "static_obstacle_move_radius_min": 2.8,
        "static_obstacle_move_radius_max": 4.5,
        "static_obstacle_shot_radius_pad": 1.0,
        "static_obstacle_shooter_gap": 4.0,
        "static_obstacle_target_gap": 4.0,
        "static_obstacle_y_below": 1.5,
        "static_obstacle_y_height": 5.0,
        "static_obstacle_chunk_margin": 80.0,
        "static_obstacle_terrain_y_tolerance": 0.0,
        "tank_collision_radius": 1.0,
        "tank_collision_enabled": True,
        "tank_collision_half_width_scale": 0.85,
        "tank_collision_half_length_scale": 0.55,
        "tank_collision_margin": 0.05,
        "tank_collision_sweep_step": 1.0,
        "ram_damage_enabled": True,
        "ram_min_closing_speed": 6.0,
        "ram_max_closing_speed": 14.0,
        "ram_damage_scale": 0.006,
        "ram_damage_cooldown_seconds": 0.75,
        "ram_damage_contact_margin": 0.0,
        "ram_friendly_damage": False,
        "ram_push_distance": 0.6,
        "tank_slope_limit_degrees": 35.0,
        "tank_slope_soft_degrees": 30.0,
        "tank_slope_sample_min_xz": 1.0,
        "tank_slope_smoothing": 0.9,
        "battle_terrain_chunk_size": 100.0,
        "battle_terrain_visible_offset": 2,
        "battle_terrain_normal_step": 1.5,
        "forced_position_broadcast_interval": 0.0,
        "forced_position_on_first_motion": False,
        "battle_engine_accel_factor": 0.18,
        "battle_brake_force_factor": 0.38,
        "battle_rolling_resistance_factor": 0.35,
        "battle_stop_speed": 0.035,
        "battle_stop_rot_speed": 0.004
    },
    "maps": {
        "fallback_arena_type_id": 1,
        "enabled_arena_type_ids": [1, 2, 4, 5, 6, 7, 8, 10, 11, 13, 15, 18, 19, 23, 28, 29, 34, 35, 37, 38],
        "geometry_paths": {
            "1": "spaces/01_karelia/",
            "2": "spaces/02_malinovka/",
            "4": "spaces/04_himmelsdorf/",
            "5": "spaces/05_prohorovka/",
            "6": "spaces/06_ensk/",
            "7": "spaces/07_lakeville/",
            "8": "spaces/08_ruinberg/",
            "10": "spaces/10_hills/",
            "11": "spaces/11_murovanka/",
            "13": "spaces/13_erlenberg/",
            "15": "spaces/15_komarin/",
            "18": "spaces/18_cliff/",
            "19": "spaces/19_monastery/",
            "23": "spaces/23_westfeld/",
            "28": "spaces/28_desert/",
            "29": "spaces/29_el_hallouf/",
            "34": "spaces/34_redshire/",
            "35": "spaces/35_steppes/",
            "37": "spaces/37_caucasus/",
            "38": "spaces/38_mannerheim_line/"
        },
        "default_spawns": {
            "1": [-273.0, 38.8, -260.0],
            "2": [-350.0, 80.0, -350.0],
            "4": [-330.0, 80.0, -330.0],
            "5": [-360.0, 80.0, -360.0],
            "6": [-260.0, 80.0, -260.0],
            "7": [-350.0, 80.0, -350.0],
            "8": [-330.0, 80.0, -330.0],
            "10": [-350.0, 80.0, -350.0],
            "11": [-360.0, 80.0, -360.0],
            "13": [-350.0, 80.0, -350.0],
            "15": [-330.0, 80.0, -330.0],
            "18": [-350.0, 80.0, -350.0],
            "19": [-350.0, 80.0, -350.0],
            "23": [-350.0, 80.0, -350.0],
            "28": [-360.0, 80.0, -360.0],
            "29": [-360.0, 80.0, -360.0],
            "34": [-360.0, 80.0, -360.0],
            "35": [-360.0, 80.0, -360.0],
            "37": [-350.0, 80.0, -350.0],
            "38": [-350.0, 80.0, -350.0]
        },
        "team_spawns": {
            "1": {
                "1": [
                    [-273.0, 38.8, -260.0],
                    [-259.0, 38.6, -273.0],
                    [-245.0, 38.4, -258.0],
                    [-270.0, 38.7, -240.0],
                    [-252.0, 38.5, -236.0],
                    [-235.0, 38.3, -246.0],
                    [-260.0, 38.5, -222.0],
                    [-240.0, 38.3, -222.0],
                    [-222.0, 38.1, -235.0]
                ],
                "2": [
                    [273.0, 38.8, 260.0],
                    [259.0, 38.6, 273.0],
                    [245.0, 38.4, 258.0],
                    [270.0, 38.7, 240.0],
                    [252.0, 38.5, 236.0],
                    [235.0, 38.3, 246.0],
                    [260.0, 38.5, 222.0],
                    [240.0, 38.3, 222.0],
                    [222.0, 38.1, 235.0]
                ]
            }
        },
        "capture_bases": {
            "1": {
                "1": [-405.14, -398.27],
                "2": [396.27, 402.37]
            }
        },
        "arena_extra_data": {
            "localized_data": {
                "en": {
                    "event_name": "Random Battle",
                    "session_name": "Standard"
                },
                "EN": {
                    "event_name": "Random Battle",
                    "session_name": "Standard"
                },
                "ru": {
                    "event_name": "Random Battle",
                    "session_name": "Standard"
                },
                "RU": {
                    "event_name": "Random Battle",
                    "session_name": "Standard"
                },
                "uk": {
                    "event_name": "Random Battle",
                    "session_name": "Standard"
                },
                "UA": {
                    "event_name": "Random Battle",
                    "session_name": "Standard"
                }
            },
            "opponents": {
                "1": {
                    "name": "Team 1"
                },
                "2": {
                    "name": "Team 2"
                }
            }
        }
    }
}

_CONFIG = None
_CONFIG_ROOT = None


def _root_path(root_path):
    if root_path is None:
        return os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    return os.path.abspath(root_path)


def _merge_dict(base, override):
    for key, value in dict(override or {}).items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge_dict(base[key], value)
        else:
            base[key] = value
    return base


def _read_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Config file must contain a JSON object: {path}")
    return data


def _set_path(config, path, value):
    current = config
    parts = path.split(".")
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


def _env_int(name, default):
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _env_float(name, default):
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _env_bool(name, default):
    value = os.environ.get(name)
    if value is None:
        return default
    return str(value).strip().lower() not in ("0", "false", "no", "off", "")


def _apply_env(config):
    env_map = (
        ("WOT_PUBLIC_HOST", "server.public_host", str),
        ("MYSQL_HOST", "database.host", str),
        ("MYSQL_PORT", "database.port", _env_int),
        ("MYSQL_USER", "database.user", str),
        ("MYSQL_PASSWORD", "database.password", str),
        ("MYSQL_DB", "database.name", str),
        ("WOT_BATTLE_TICK_HZ", "battle.tick_hz", _env_float),
        ("WOT_BATTLE_VERBOSE_DEBUG", "battle.verbose_debug", _env_bool),
        ("WOT_CLIENT_SHOT_DAMAGE_EFFECTS", "combat.client_shot_damage_effects", _env_bool),
        ("WOT_PRIVATE_KEY", "server.private_key_path", str),
        ("WOT_CLIENT_ROOT", "paths.client_root", str)
    )
    for env_name, path, caster in env_map:
        if env_name not in os.environ:
            continue
        if caster is str:
            value = os.environ.get(env_name)
        else:
            value = caster(env_name, None)
        if value is not None:
            _set_path(config, path, value)
    return config


def _apply_env_json(config, root):
    """Read config/env.json and set public_host based on isProduction flag."""
    env_path = os.path.join(root, "config", "env.json")
    if not os.path.exists(env_path):
        return config
    try:
        env_data = _read_json(env_path)
    except Exception:
        return config
    env_section = env_data.get("env", {})
    is_production = env_section.get("isProduction", False)
    if is_production:
        host = env_section.get("production_host", "63.185.68.216")
    else:
        host = env_section.get("local_host", "127.0.0.1")
    _set_path(config, "server.public_host", host)
    return config


def load_config(root_path=None, force=False):
    global _CONFIG, _CONFIG_ROOT
    root = _root_path(root_path)
    if _CONFIG is not None and _CONFIG_ROOT == root and not force:
        return _CONFIG
    config = copy.deepcopy(DEFAULT_CONFIG)
    config_dir = os.path.join(root, "config")
    for pattern in ("*.example.json", "*.json", "*.local.json"):
        for path in sorted(glob.glob(os.path.join(config_dir, pattern))):
            name = os.path.basename(path)
            if pattern == "*.json" and (
                    name.endswith(".example.json") or
                    name.endswith(".local.json")):
                continue
            _merge_dict(config, _read_json(path))
    _apply_env_json(config, root)
    _apply_env(config)
    _CONFIG = config
    _CONFIG_ROOT = root
    return config


def get_config(root_path=None):
    return load_config(root_path)


def get_value(config, path, default=None):
    current = config
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def resolve_existing_path(root_path, configured_path, legacy_name=None):
    candidates = []
    if configured_path:
        if os.path.isabs(configured_path):
            candidates.append(os.path.normpath(configured_path))
        else:
            candidates.append(os.path.normpath(os.path.join(root_path, configured_path)))
    if legacy_name:
        candidates.append(os.path.normpath(os.path.join(root_path, legacy_name)))
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return candidates[0] if candidates else None
