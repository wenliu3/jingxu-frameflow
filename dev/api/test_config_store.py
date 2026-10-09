"""Offline regression tests: all config files live in temporary directories."""
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from server.config_store import ConfigStore


class ConfigStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "service_config.json"
        self.defaults = {"text_api_key": "", "video_lora": "default-lora", "comfyui_url": "", "text_model": "default"}
        self.store = ConfigStore(self.path, self.defaults, {k: k.upper() for k in self.defaults},
                                 environ={"TEXT_API_KEY": "fallback-key"})

    def test_explicit_blank_clears_fallback_and_lora(self):
        self.assertEqual(self.store.get()["text_api_key"], "fallback-key")
        result = self.store.patch({"text_api_key": "", "video_lora": ""})
        self.assertEqual(result["text_api_key"], "")
        self.assertEqual(result["video_lora"], "")
        restored = ConfigStore(self.path, self.defaults, {k: k.upper() for k in self.defaults}, environ={"TEXT_API_KEY": "fallback-key"})
        self.assertEqual(restored.get()["text_api_key"], "")

    def test_patch_preserves_unrelated_fields(self):
        self.store.patch({"text_api_key": "saved-key"})
        result = self.store.patch({"text_model": "next-model"})
        self.assertEqual(result["text_api_key"], "saved-key")
        self.assertEqual(result["text_model"], "next-model")

    def test_cache_reloads_external_changes_and_returns_copy(self):
        self.store.patch({"text_model": "first"})
        with patch.object(self.store, "_read", wraps=self.store._read) as read:
            self.store.get()["text_model"] = "mutated"
            self.assertEqual(self.store.get()["text_model"], "first")
            read.assert_not_called()
            self.path.write_text(json.dumps({"text_model": "changed-on-disk"}), encoding="utf-8")
            self.assertEqual(self.store.get()["text_model"], "changed-on-disk")
            self.store.get()
            read.assert_called_once()

    def test_failed_replace_preserves_file_and_runtime(self):
        self.store.patch({"text_model": "keep"})
        before = self.path.read_bytes()
        apply = Mock()
        with patch("server.config_store.os.replace", side_effect=OSError("simulated failure")):
            with self.assertRaises(OSError):
                self.store.patch({"text_model": "lost"}, apply=apply)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.store.get()["text_model"], "keep")
        self.assertFalse(list(self.path.parent.glob("*.tmp")))
        apply.assert_not_called()

    def test_corrupt_config_is_not_overwritten(self):
        self.path.write_text("{invalid", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store.patch({"text_model": "lost"})
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{invalid")

    def test_concurrent_patches_preserve_each_field(self):
        defaults = {f"field_{i}": "" for i in range(20)}
        store = ConfigStore(self.path, defaults, {key: key for key in defaults}, environ={})
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda i: store.patch({f"field_{i}": str(i)}), range(20)))
        self.assertEqual(store.get(), {f"field_{i}": str(i) for i in range(20)})

    def test_legacy_address_only_fills_missing_field(self):
        legacy = self.path.parent / "legacy.json"
        legacy.write_text(json.dumps({"url": "http://legacy:8188"}), encoding="utf-8")
        store = ConfigStore(self.path, self.defaults, {k: k for k in self.defaults}, legacy_path=legacy, environ={})
        self.assertEqual(store.get()["comfyui_url"], "http://legacy:8188")
        self.assertEqual(store.patch({"comfyui_url": ""})["comfyui_url"], "")


class ConfigApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server.app as app_module
        cls.module = app_module

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "service_config.json"
        self.store = ConfigStore(self.path, self.module.SERVICE_DEFAULTS, self.module._ENV_OF, environ={})
        self.store.patch({"text_api_key": "test-only", "text_model": "test-model"})
        for name, value in (("SERVICE_STORE", self.store), ("_apply_service_config", Mock()),
                            ("requests.get", Mock(side_effect=AssertionError("No network during save")))):
            target = "server.app." + name
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(self.module.app)
        self.addCleanup(self.client.close)

    def test_partial_save_is_immediate_and_preserves_keys(self):
        response = self.client.patch("/api/config", json={"video_megapixels": "0.7"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["config"]["text_api_key"], "test-only")
        self.module.requests.get.assert_not_called()
        self.module._apply_service_config.assert_called_once()

    def test_invalid_settings_leave_previous_config_intact(self):
        before = self.path.read_bytes()
        for values in ({"video_steps": "1.5"}, {"video_megapixels": "nan"}, {"video_timeout_s": "10"},
                       {"video_workflow": "unknown"}, {"video_workflow": "ref2va"},
                       {"video_ref_image_size": "unknown"}, {"unknown": "x"}):
            with self.subTest(values=values):
                self.assertEqual(self.client.patch("/api/config", json=values).status_code, 422)
                self.assertEqual(self.path.read_bytes(), before)

    def test_disabling_lora_really_clears_the_saved_value(self):
        response = self.client.patch("/api/config", json={"video_lora": "", "video_steps": "20"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/config").json()["video_lora"], "")

    def test_reference_precision_roundtrips_without_changing_other_settings(self):
        response = self.client.patch("/api/config", json={"video_ref_image_size": "max"})
        self.assertEqual(response.status_code, 200)
        cfg = self.client.get("/api/config").json()
        self.assertEqual(cfg["video_ref_image_size"], "max")
        self.assertEqual(cfg["text_api_key"], "test-only")
        self.assertEqual(cfg["video_steps"], self.module.SERVICE_DEFAULTS["video_steps"])

    def test_connection_test_never_saves_draft(self):
        before = self.path.read_bytes()
        self.module.requests.get.side_effect = None
        self.module.requests.get.return_value = Mock(json=Mock(return_value={"system": {"os": "test"}}))
        response = self.client.post("/api/config/test", json={"comfyui_url": "http://localhost:8188/"})
        self.assertTrue(response.json()["reachable"])
        self.module.requests.get.assert_called_once_with("http://localhost:8188/system_stats", timeout=(3, 5))
        self.assertEqual(self.path.read_bytes(), before)

    def test_non_comfy_response_is_not_reported_connected(self):
        self.module.requests.get.side_effect = None
        self.module.requests.get.return_value = Mock(json=Mock(return_value={"ok": True}))
        response = self.client.post("/api/config/test", json={"comfyui_url": "http://localhost:8188"})
        self.assertFalse(response.json()["reachable"])

    def test_invalid_connection_address_never_requests_network(self):
        for address in ("file:///private", "http://[broken", "http://localhost:99999", "http://localhost:abc"):
            with self.subTest(address=address):
                response = self.client.post("/api/config/test", json={"comfyui_url": address})
                self.assertEqual(response.status_code, 422)
        self.module.requests.get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
