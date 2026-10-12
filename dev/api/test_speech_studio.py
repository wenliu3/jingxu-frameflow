"""Speech controls, account catalogs and preview caching; no remote calls."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

import tts
from speech_controls import SpeechSettings
from server.speech_studio import VoiceCatalog, register_speech_routes


class SpeechStudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {"AUDIO_PROVIDER": "minimax", "AUDIO_API_KEY": "fixture-key",
            "AUDIO_BASE_URL": "https://fixture.invalid/v1", "AUDIO_MODEL": "speech-2.8-hd"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def response(self):
        response = MagicMock()
        response.json.return_value = {"base_resp": {"status_code": 0}, "system_voice": [
            {"voice_id": "female-shaonv", "voice_name": "Young Girl", "description": ["female Mandarin voice"]},
            {"voice_id": "English_narrator", "voice_name": "Narrator", "description": ["male English voice"]}],
            "voice_cloning": [{"voice_id": "my-voice", "description": []}]}
        return response

    def test_catalog_sync_deduplicates_and_is_scoped_to_account(self):
        catalog = VoiceCatalog()
        with patch("requests.request", return_value=self.response()) as request:
            result = catalog.get()
            self.assertEqual(result["source"], "synced")
            self.assertEqual(len(result["items"]), 3)
            self.assertEqual(result["items"][0]["cn"], "少女")
            self.assertEqual(result["items"][1]["locale"], "英语")
            self.assertEqual(result["items"][2]["source"], "cloned")
            result["items"].clear()
            self.assertEqual(len(catalog.get()["items"]), 3)
            self.assertEqual(request.call_count, 1)
            os.environ["AUDIO_API_KEY"] = "another-fixture-account"
            catalog.get()
            self.assertEqual(request.call_count, 2)

    def test_catalog_falls_back_and_failed_refresh_keeps_synced_voices(self):
        catalog = VoiceCatalog()
        with patch("requests.request", return_value=self.response()):
            catalog.get()
        with patch("requests.request", side_effect=ValueError("Bad JSON")):
            result = catalog.get(refresh=True)
            self.assertEqual(result["source"], "synced")
            self.assertIn("上次同步", result["notice"])
            os.environ["AUDIO_API_KEY"] = "different-account"
            fallback = catalog.get()
            self.assertEqual(fallback["source"], "preset")
            self.assertIn("内置音色", fallback["notice"])
            self.assertNotIn("my-voice", {v["id"] for v in fallback["items"]})

    def test_real_minimax_payload_uses_all_controls(self):
        controls = SpeechSettings(model="speech-2.8-turbo", speed=1.2, volume=2, pitch=-3,
            emotion="calm", language="Chinese", effect_pitch=12, intensity=-5, timbre=20, sound_effect="robotic")
        response = MagicMock()
        response.json.return_value = {"base_resp": {"status_code": 0}, "data": {"audio": b"fixture".hex()}}
        with patch("requests.request", return_value=response) as request:
            output = self.root / "audio.mp3"
            tts.synth("你好<#0.5#>世界(laughs)", "female-shaonv", str(output), settings=controls)
            self.assertEqual(output.read_bytes(), b"fixture")
            payload = request.call_args.kwargs["json"]
            self.assertEqual(payload["model"], "speech-2.8-turbo")
            self.assertEqual(payload["voice_setting"], {"voice_id": "female-shaonv", "speed": 1.2, "vol": 2, "pitch": -3, "emotion": "calm"})
            self.assertEqual(payload["language_boost"], "Chinese")
            self.assertEqual(payload["voice_modify"], {"pitch": 12, "intensity": -5, "timbre": 20, "sound_effects": "robotic"})

    def test_edge_maps_supported_controls_and_rejects_unsupported(self):
        os.environ["AUDIO_PROVIDER"] = "edge"
        with patch("edge_tts.Communicate") as communicate:
            communicate.return_value.save = AsyncMock()
            tts.synth("Hello", "voice", str(self.root / "edge.mp3"), settings={"speed": .8, "volume": .5, "pitch": 2})
            communicate.assert_called_once_with("Hello", "voice", rate="-20%", volume="-50%", pitch="+20Hz")
            with self.assertRaises(ValueError):
                tts.synth("Hello", "voice", str(self.root / "edge.mp3"), settings={"sound_effect": "robotic"})
            self.assertEqual(communicate.call_count, 1)

    def test_out_of_range_and_unknown_controls_are_rejected(self):
        for values in [{"speed": 4}, {"volume": 0}, {"volume": float("nan")}, {"pitch": 1.5},
                {"intensity": 101}, {"sound_effect": "made-up"}, {"model": "fake"}, {"unknown": 1}]:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                SpeechSettings(**values)

    def test_preview_cached_by_controls_does_not_publish_a_material(self):
        app = FastAPI()
        catalog = MagicMock()
        catalog.get.return_value = {"items": [{"id": "my-voice"}]}
        register_speech_routes(app, lambda _: {}, lambda _: str(self.root), catalog)
        with TestClient(app) as client, patch("tts.synth", side_effect=lambda text, voice, path, **kwargs: Path(path).write_bytes(b"fixture")) as synth:
            payload = {"voice_id": "my-voice", "speech_settings": {"speed": 1.2}}
            first = client.post("/api/tasks/project/speech-preview", json=payload)
            self.assertEqual(first.status_code, 200)
            self.assertFalse(first.json()["cached"])
            self.assertTrue(client.post("/api/tasks/project/speech-preview", json=payload).json()["cached"])
            self.assertEqual(synth.call_count, 1)
            self.assertEqual(client.post("/api/tasks/project/speech-preview", json={**payload, "voice_id": "missing"}).status_code, 422)
            client.post("/api/tasks/project/speech-preview", json={**payload, "speech_settings": {"speed": 1.3}})
            self.assertEqual(synth.call_count, 2)
            self.assertEqual({path.name for path in self.root.iterdir()}, {"voice_previews"})


if __name__ == "__main__":
    unittest.main()
