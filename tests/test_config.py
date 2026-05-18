import json
import os
import tempfile
import unittest
from unittest import mock

from server_core.config import get_config, load_config, resolve_existing_path


class ConfigTests(unittest.TestCase):
    def test_local_config_overrides_example_and_env_overrides_local(self):
        with tempfile.TemporaryDirectory() as root:
            config_dir = os.path.join(root, "config")
            os.makedirs(config_dir)
            with open(os.path.join(config_dir, "server.example.json"), "w", encoding="utf-8") as handle:
                json.dump({
                    "server": {"public_host": "example.invalid"},
                    "database": {"port": 3307},
                    "battle": {"tick_hz": 30}
                }, handle)
            with open(os.path.join(config_dir, "server.local.json"), "w", encoding="utf-8") as handle:
                json.dump({
                    "server": {"public_host": "local.invalid"},
                    "database": {"port": 3308}
                }, handle)
            env = {
                "WOT_PUBLIC_HOST": "env.invalid",
                "MYSQL_PORT": "4406",
                "WOT_BATTLE_TICK_HZ": "75"
            }
            with mock.patch.dict(os.environ, env, clear=False):
                config = load_config(root, force=True)
            self.assertEqual(config["server"]["public_host"], "env.invalid")
            self.assertEqual(config["database"]["port"], 4406)
            self.assertEqual(config["battle"]["tick_hz"], 75.0)

    def test_get_config_uses_loaded_cache_for_same_root(self):
        with tempfile.TemporaryDirectory() as root:
            config = load_config(root, force=True)
            self.assertIs(get_config(root), config)

    def test_plain_json_config_overrides_legacy_example(self):
        with tempfile.TemporaryDirectory() as root:
            config_dir = os.path.join(root, "config")
            os.makedirs(config_dir)
            with open(os.path.join(config_dir, "battle.example.json"), "w", encoding="utf-8") as handle:
                json.dump({"battle": {"tick_hz": 30}}, handle)
            with open(os.path.join(config_dir, "battle.json"), "w", encoding="utf-8") as handle:
                json.dump({"battle": {"tick_hz": 60}}, handle)

            config = load_config(root, force=True)

            self.assertEqual(config["battle"]["tick_hz"], 60)

    def test_resolve_existing_path_prefers_configured_path_then_legacy(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "data"))
            legacy = os.path.join(root, "_vehicles.json")
            configured = os.path.join(root, "data", "_vehicles.json")
            with open(legacy, "w", encoding="utf-8") as handle:
                handle.write("{}")
            self.assertEqual(
                resolve_existing_path(root, "data/_vehicles.json", "_vehicles.json"),
                legacy)
            with open(configured, "w", encoding="utf-8") as handle:
                handle.write("{}")
            self.assertEqual(
                resolve_existing_path(root, "data/_vehicles.json", "_vehicles.json"),
                configured)


class EmulatorCompatibilityTests(unittest.TestCase):
    def test_emulator_facade_exports_existing_api(self):
        import emulator
        self.assertTrue(hasattr(emulator, "main"))
        self.assertTrue(hasattr(emulator, "load_all_vehicles"))
        self.assertTrue(hasattr(emulator, "ARENA_TYPE_KARELIA"))

    def test_disabled_map_falls_back_to_configured_arena(self):
        import emulator
        old_enabled = emulator.ENABLED_ARENA_TYPE_IDS
        old_fallback = emulator.ARENA_TYPE_FALLBACK
        try:
            emulator.ENABLED_ARENA_TYPE_IDS = {emulator.ARENA_TYPE_KARELIA}
            emulator.ARENA_TYPE_FALLBACK = emulator.ARENA_TYPE_KARELIA
            self.assertEqual(
                emulator.normalize_arena_type_id(2),
                emulator.ARENA_TYPE_KARELIA)
            self.assertEqual(
                emulator.normalize_arena_type_id(emulator.ARENA_TYPE_KARELIA),
                emulator.ARENA_TYPE_KARELIA)
        finally:
            emulator.ENABLED_ARENA_TYPE_IDS = old_enabled
            emulator.ARENA_TYPE_FALLBACK = old_fallback

    def test_data_cache_reset_api_exists(self):
        import emulator
        emulator.reset_data_caches()
        self.assertIsNone(emulator._TANKMEN_CONFIG_CACHE)
        self.assertIsNone(emulator._ARTEFACTS_CONFIG_CACHE)


if __name__ == "__main__":
    unittest.main()
