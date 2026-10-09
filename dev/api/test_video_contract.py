"""Offline generation contract tests: temporary files and fake workers only."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from pydantic import ValidationError
from schemas import Project
from server.video_policy import resolve_resolution
from video_provider import ComfyUIVideoProvider, _save_video_stream


class VideoContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server.app as module
        cls.module = module

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image = self.root / "test.png"
        self.image.write_bytes(b"test image")
        self.project = Project.from_dict({"title": "Test", "characters": [{"name": "Hero", "image_path": str(self.image), "sheet": str(self.image), "voice_sample": "voice.mp3"}], "assets": [
            {"kind": "image", "name": "First", "images": [str(self.image)]},
            {"kind": "image", "name": "Second", "images": [str(self.root / "second.png")]},
            {"kind": "scene", "name": "Scene", "images": [str(self.image)]}]})
        (self.root / "second.png").write_bytes(b"second image")
        self.cfg = {**self.module.SERVICE_DEFAULTS, "comfyui_url": "http://127.0.0.1:1", "video_lora": "", "video_steps": "20", "video_megapixels": "0.7"}
        self.task = {"task_id": "abcdef123456", "status": "succeeded"}
        self.runners = []
        self.calls = []

        def generate(**kwargs):
            self.calls.append(kwargs)
            Path(kwargs["out_path"]).write_bytes(b"fake completed video")
        self.provider = Mock(generate=Mock(side_effect=generate))
        self.factory = Mock(return_value=self.provider)
        self.writer = Mock(side_effect=AssertionError("No model calls"))
        def fake_thread(*, target, **kwargs):
            self.runners.append(target)
            return Mock(start=Mock())
        for name, value in (("_task", Mock(return_value=self.task)), ("_reload", Mock(return_value=(self.project, []))),
                            ("_out_dir", Mock(return_value=str(self.root))), ("_load_service_config", Mock(side_effect=lambda: dict(self.cfg))),
                            ("_make_video_provider", self.factory), ("SEGMENT_JOBS", {}),
                            ("threading", SimpleNamespace(Thread=fake_thread)), ("agents.compose_segment_prompt", self.writer)):
            patcher = patch("server.app." + name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(self.module.app)
        self.addCleanup(self.client.close)
        self.url = "/api/tasks/abcdef123456/segment/video"

    def start(self, **extra):
        return self.client.post(self.url, json={"frames": ["image:0"], "prompt": "A cinematic shot.", **extra})

    def record(self, response):
        return json.loads((self.root / "segments" / f"seg_{response.json()['job_id']}.json").read_text(encoding="utf-8"))

    def test_default_resolution_and_job_config_are_frozen_at_submission(self):
        response = self.start()
        self.assertEqual(response.status_code, 200)
        self.cfg["video_megapixels"] = "0.98"
        self.cfg["video_workflow"] = "ref2va"
        self.runners[0]()
        record = self.record(response)
        self.assertEqual(record["megapixels"], 0.7)
        self.assertEqual(record["resolution_source"], "default")
        self.assertEqual(record["video_workflow"], "i2v")
        self.assertEqual(self.calls[0]["megapixels"], 0.7)
        self.assertEqual(self.factory.call_args.args[0]["video_workflow"], "i2v")

    def test_per_clip_override_does_not_change_defaults(self):
        response = self.start(megapixels=0.9)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.record(response)["megapixels"], 0.9)
        self.assertEqual(self.record(response)["resolution_source"], "override")
        self.assertEqual(self.cfg["video_megapixels"], "0.7")

    def test_job_records_and_freezes_reference_precision_at_submission(self):
        self.cfg.update(video_workflow="ref2va", video_ref_image_size="max")
        response = self.start()
        self.assertEqual(response.status_code, 200)
        self.cfg["video_ref_image_size"] = "match"
        self.runners[0]()
        self.assertEqual(self.record(response)["video_ref_image_size"], "max")
        self.assertEqual(self.factory.call_args.args[0]["video_ref_image_size"], "max")

    def test_api_does_not_claim_h3_parameters_or_reference_workflow(self):
        self.cfg.update(video_backend="api", video_workflow="ref2va", video_api_url="http://127.0.0.1:1", video_api_key="fake", video_api_model="fake", video_steps="12", video_lora=next(iter(self.module._LORA_STEPS)))
        response = self.start(megapixels=0.9)
        self.assertEqual(response.status_code, 200)
        record = self.record(response)
        self.assertIsNone(record["megapixels"])
        self.assertIsNone(record["duration"])
        self.assertEqual(record["planned_duration"], 10)
        self.assertEqual(record["video_workflow"], "api")
        self.assertEqual(record["warning"], "")
        composed = self.client.post("/api/tasks/abcdef123456/compose", json={"images": ["First"], "video_prompt": "English."})
        self.assertEqual(composed.status_code, 200)
        self.assertEqual(composed.json()["video_workflow"], "api")
        self.assertEqual(composed.json()["prompt"], "English.")

    def test_workflow_changed_after_composing_is_rejected(self):
        response = self.start(expected_workflow="ref2va")
        self.assertEqual(response.status_code, 409)
        self.factory.assert_not_called()

    def test_missing_duplicate_changed_or_excess_images_do_not_start_a_job(self):
        for extra in ({"frames": ["image:99"]}, {"frames": ["image:0", "image:0"]},
                      {"frames": ["image:0", "image:1"]}, {"first_frame": "image:99"},
                      {"frame_names": {"image:0": "Changed"}}, {"frames": ["image:0"] * 10}):
            with self.subTest(extra=extra):
                response = self.start(**extra)
                self.assertIn(response.status_code, (409, 422))
        self.factory.assert_not_called()

    def test_explicit_i2v_tail_is_supported_but_ref_and_api_tails_are_rejected(self):
        response = self.start(frames=["image:0", "image:1"], last_frame="image:1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.record(response)["mode"], "flf")
        self.cfg["video_workflow"] = "ref2va"
        self.assertEqual(self.start(last_frame="image:1").status_code, 422)
        self.cfg.update(video_backend="api", video_api_url="http://127.0.0.1:1", video_api_key="fake", video_api_model="fake")
        self.assertEqual(self.start(last_frame="image:1").status_code, 422)

    def test_invalid_numeric_parameters_are_rejected_instead_of_clamped(self):
        for extra in ({"duration": 0.5}, {"duration": 16}, {"megapixels": 1.5}, {"megapixels": 0}):
            with self.subTest(extra=extra):
                self.assertEqual(self.start(**extra).status_code, 422)
        for key in ("duration", "megapixels"):
            with self.assertRaises(ValidationError):
                self.module.SegmentVideoBody.model_validate({key: float("nan")})
        self.factory.assert_not_called()

    def test_one_to_fifteen_seconds_compose_and_reach_generation(self):
        for workflow in ("i2v", "ref2va"):
            self.cfg["video_workflow"] = workflow
            for duration in (1, 1.5, 3, 15):
                with self.subTest(workflow=workflow, duration=duration):
                    composed = self.client.post("/api/tasks/abcdef123456/compose", json={
                        "images": ["First"], "video_prompt": "[Shot 1] A cinematic shot.", "duration": duration})
                    self.assertEqual(composed.status_code, 200, composed.text)
                    response = self.start(prompt=composed.json()["prompt"], duration=duration, exact_duration=True)
                    self.assertEqual(response.status_code, 200, response.text)
                    self.runners[-1]()
                    self.assertEqual(self.calls[-1]["duration"], duration)
                    self.assertTrue(self.calls[-1]["exact_duration"])
                    self.assertEqual(self.record(response)["duration"], duration)
        capabilities = self.client.get("/api/video-capabilities").json()
        self.assertEqual((capabilities["duration_min"], capabilities["duration_max"]), (1, 15))

    def test_composition_rejects_missing_and_audio_inputs_before_model_call(self):
        for payload in ({"images": ["Missing"], "description": "test"},
                        {"images": ["First"], "audios": ["Audio"], "description": "test"},
                        {"images": ["First", "Second"], "description": "test"}):
            self.assertEqual(self.client.post("/api/tasks/abcdef123456/compose", json=payload).status_code, 422)
        self.writer.assert_not_called()

    def test_reference_composition_does_not_claim_unconnected_audio(self):
        self.cfg["video_workflow"] = "ref2va"
        response = self.client.post("/api/tasks/abcdef123456/compose", json={"characters": ["Hero"], "video_prompt": "English.", "use_voice": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["video_workflow"], "ref2va")
        self.assertTrue(all(slot["kind"] == "image" for slot in response.json()["slots"]))
        self.assertNotIn("<Audio 1>", response.json()["prompt"])

    def test_reference_prompt_rejects_invalid_labels_and_timing_before_start(self):
        self.cfg["video_workflow"] = "ref2va"
        for prompt in ("[Shot 1] Follow <Video 1>.", "[Shot 1] Use <Audio 1>.",
                       "[Shot 1] Copy <Picture 2>.", "[Shot 1] a [Shot 2] At 00:06.501, b"):
            with self.subTest(prompt=prompt):
                composed = self.client.post("/api/tasks/abcdef123456/compose", json={"images": ["First"], "duration": 6.5, "video_prompt": prompt})
                self.assertEqual(composed.status_code, 422)
                response = self.start(prompt=prompt, duration=6.5)
                self.assertEqual(response.status_code, 422)
        self.writer.assert_not_called()
        self.factory.assert_not_called()
        self.assertEqual(self.runners, [])

    def test_reference_order_and_complete_prompt_survive_submission(self):
        self.cfg["video_workflow"] = "ref2va"
        composed = self.client.post("/api/tasks/abcdef123456/compose", json={"images": ["Second", "First"],
            "video_prompt": "[Shot 1] Use the composition of <Picture 2>.", "duration": 6.5})
        self.assertEqual(composed.status_code, 200)
        self.assertEqual([s["label"] for s in composed.json()["slots"]], ["Second", "First"])
        prompt = composed.json()["prompt"]
        response = self.start(frames=["image:1", "image:0"], prompt=prompt, duration=6.5)
        self.assertEqual(response.status_code, 200)
        self.runners[0]()
        self.assertEqual(self.calls[0]["video_prompt"], prompt)
        self.assertEqual(self.calls[0]["ref_image_paths"], [str(self.root / "second.png"), str(self.image)])
        self.writer.assert_not_called()

    def test_composition_exposes_reminders_and_passes_context_and_appearance(self):
        self.cfg["video_workflow"] = "ref2va"
        self.project.characters[0].anchor_en = "a woman with short hair"
        self.writer.side_effect = None
        self.writer.return_value = {"video_prompt": "Natural light. [Shot 1] <Subject 1> walks.",
            "soundscape": "Footsteps.", "warnings": ["确认动作细节"]}
        response = self.client.post("/api/tasks/abcdef123456/compose", json={"characters": ["Hero"],
            "description": "她走过房间", "context": "上一镜切到室外", "duration": 6.5})
        self.assertEqual(response.status_code, 200)
        self.assertIn("确认动作细节", response.json()["warnings"])
        kwargs = self.writer.call_args.kwargs
        self.assertEqual(kwargs["description"], "她走过房间")
        self.assertEqual(kwargs["context"], "上一镜切到室外")
        self.assertTrue(any("a woman with short hair" in line for line in kwargs["material_lines"]))

    def test_composition_checks_actual_frame_instead_of_reference_sheet(self):
        hero = self.project.characters[0]
        hero.image_path = ""
        response = self.client.post("/api/tasks/abcdef123456/compose", json={"characters": ["Hero"], "description": "test"})
        self.assertEqual(response.status_code, 422)
        self.writer.assert_not_called()

        hero.image_path = str(self.image)
        hero.sheet = str(self.root / "missing-sheet.png")
        for workflow in ("i2v", "ref2va"):
            self.cfg["video_workflow"] = workflow
            response = self.client.post("/api/tasks/abcdef123456/compose", json={"characters": ["Hero"], "video_prompt": "English."})
            self.assertEqual(response.status_code, 200)

    def test_material_mentions_follow_actual_reference_order_without_a_text_model(self):
        self.cfg["video_workflow"] = "ref2va"
        for images, expected in [(["Second", "First"], "<Picture 2>"), (["First", "Second"], "<Picture 1>")]:
            response = self.client.post("/api/tasks/abcdef123456/compose", json={"images": images,
                "video_prompt": "[Shot 1] Follow the lighting in @{image:First}."})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn(expected, response.json()["video_prompt"])
            self.assertNotIn("@{", response.json()["prompt"])
        self.writer.assert_not_called()

    def test_ai_mentions_resolve_to_subjects_and_unconnected_mentions_are_rejected(self):
        self.cfg["video_workflow"] = "ref2va"
        self.writer.side_effect = None
        self.writer.return_value = {"video_prompt": "Natural light. [Shot 1] <Subject 1> walks.", "soundscape": "Footsteps."}
        response = self.client.post("/api/tasks/abcdef123456/compose", json={"characters": ["Hero"],
            "description": "@{character:Hero}走近镜头"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.writer.call_args.kwargs["description"], "Hero（<Subject 1>）走近镜头")
        self.writer.reset_mock()
        for field in ("description", "video_prompt"):
            response = self.client.post("/api/tasks/abcdef123456/compose", json={"images": ["First"], field: "@{image:Second}"})
            self.assertEqual(response.status_code, 422)
            self.assertIn("Second", response.json()["detail"])
        self.writer.assert_not_called()

    def test_empty_provider_output_marks_job_failed(self):
        self.provider.generate.side_effect = lambda **kwargs: None
        response = self.start()
        self.runners[0]()
        job = self.module.SEGMENT_JOBS[response.json()["job_id"]]
        self.assertEqual(job["status"], "failed")

    def test_legacy_record_list_uses_workflow_instead_of_incorrect_mode(self):
        directory = self.root / "segments"
        directory.mkdir()
        (directory / "old.mp4").write_bytes(b"completed video")
        metadata = {"mode": "i2v", "video_workflow": "ref2va", "video_backend": "comfyui"}
        sidecar = directory / "old.json"
        sidecar.write_text(json.dumps(metadata), encoding="utf-8")
        for backend, expected in (("comfyui", "ref2va"), ("api", "api")):
            metadata["video_backend"] = backend
            sidecar.write_text(json.dumps(metadata), encoding="utf-8")
            before = sidecar.read_bytes()
            response = self.client.get("/api/tasks/abcdef123456/segments")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["items"][0]["mode"], expected)
            self.assertEqual(sidecar.read_bytes(), before)

    def test_candidate_generation_uses_unique_seeds_files_and_retains_partial_success(self):
        response = self.start(candidate_count=4, duration=6.5, ratio="16:9", resolution="480p", seed=123, generate_audio=False, exact_duration=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 4)
        seeds = [c["seed"] for c in response.json()["candidates"]]
        self.assertEqual(seeds, [123, 124, 125, 126])
        def partially_fail(**kwargs):
            self.calls.append(kwargs)
            if kwargs["seed"] == 124:
                raise RuntimeError("one candidate failed")
            Path(kwargs["out_path"]).write_bytes(b"completed video")
        self.provider.generate.side_effect = partially_fail
        self.runners[0]()
        job_id = response.json()["job_id"]
        result = self.client.get(f"/api/segment-jobs/{job_id}?task_id=abcdef123456").json()
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["done"], 4)
        self.assertEqual([r["status"] for r in result["results"]], ["succeeded", "failed", "succeeded", "succeeded"])
        self.assertEqual(len({c["out_path"] for c in self.calls}), 4)
        self.assertTrue(all(c["duration"] == 6.5 and not c["generate_audio"] and c["exact_duration"] for c in self.calls))
        self.assertEqual((self.calls[0]["width"], self.calls[0]["height"]), (854, 480))
        for candidate in result["results"]:
            sidecar = json.loads((self.root / "segments" / Path(candidate["file"]).with_suffix(".json")).read_text(encoding="utf-8"))
            self.assertEqual(sidecar["seed"], candidate["seed"])
            self.assertEqual(sidecar["resolution_source"], "preset")
        # Recovery reads the result from disk and never starts new remote generations.
        self.module.SEGMENT_JOBS.clear()
        recovered = self.client.get(f"/api/segment-jobs/{job_id}?task_id=abcdef123456").json()
        self.assertEqual(recovered["results"], result["results"])
        self.assertEqual(len(self.calls), 4)

    def test_controls_reject_invalid_values_and_unsupported_api_settings(self):
        for extra in ({"candidate_count": 3}, {"candidate_count": 20}, {"ratio": "2:3"}, {"resolution": "4k"}, {"seed": -1}, {"seed": 2**31}, {"seed": 1.5}):
            self.assertEqual(self.start(**extra).status_code, 422, extra)
        self.cfg.update(video_backend="api", video_api_url="http://local", video_api_key="fake", video_api_model="fake")
        for extra in ({"ratio": "16:9"}, {"resolution": "720p"}, {"exact_duration": True}, {"seed": 5}):
            self.assertEqual(self.start(**extra).status_code, 422, extra)
        self.factory.assert_not_called()

    def test_restart_interrupts_pending_candidates_without_resubmitting(self):
        response = self.start(candidate_count=2)
        job_id = response.json()["job_id"]
        self.module.SEGMENT_JOBS.clear()
        recovered = self.client.get(f"/api/segment-jobs/{job_id}?task_id=abcdef123456").json()
        self.assertEqual(recovered["status"], "interrupted")
        self.assertTrue(all(r["status"] == "interrupted" for r in recovered["results"]))
        self.assertEqual(len(self.runners), 1)
        self.assertEqual(len(self.calls), 0)

    def test_progress_save_failure_prevents_generation(self):
        with patch("server.app.save_job", side_effect=OSError("disk full")):
            self.assertEqual(self.start(candidate_count=2).status_code, 500)
        self.assertEqual(self.runners, [])
        self.assertEqual(self.calls, [])

    def test_candidate_record_save_failure_stops_remaining_remote_submissions(self):
        response = self.start(candidate_count=4)
        with patch("server.segment_generation.save_candidate", side_effect=OSError("disk full")):
            self.runners[0]()
        result = self.client.get(f"/api/segment-jobs/{response.json()['job_id']}").json()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual([r["status"] for r in result["results"]], ["succeeded", "interrupted", "interrupted", "interrupted"])
        self.assertTrue(result["results"][0]["video_url"])

    def test_candidate_sidecar_replacement_failure_preserves_complete_previous_record(self):
        from server.segment_job_store import save_candidate
        response = self.start()
        candidate = self.module.SEGMENT_JOBS[response.json()["job_id"]]["results"][0]
        record_path = Path(candidate["out_path"]).with_suffix(".json")
        before = record_path.read_bytes()
        candidate["record"]["status"] = "running"
        with patch("server.segment_job_store.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                save_candidate(str(self.root), candidate)
        self.assertEqual(record_path.read_bytes(), before)
        self.assertEqual(list(record_path.parent.glob("*.tmp")), [])
        with self.assertRaises(ValueError):
            save_candidate(str(self.root), {"file": "../escape.mp4", "record": {}})


class VideoProviderTests(unittest.TestCase):
    def test_ratio_seed_and_audio_are_wired_into_both_comfyui_workflows(self):
        for template in ("h3_i2v_api.json", "h3_r2v_api.json"):
            provider = ComfyUIVideoProvider(base_url="http://local", workflow_path=str(Path(__file__).parents[2] / "comfyui" / template), lora="")
            graph = provider._build_workflow("English.", 6.5, "image.png", width=720, height=1280, seed=321, generate_audio=False)
            h3 = next(v for v in graph.values() if v["class_type"].startswith("MiniMaxH3"))
            self.assertEqual((h3["inputs"]["width"], h3["inputs"]["height"]), (704, 1280))
            self.assertEqual(h3["inputs"]["length"] % 17, 5)
            self.assertEqual(graph["15"]["inputs"]["noise_seed"], 321)
            self.assertNotIn("audio", graph["91"]["inputs"])
            if "i2v" in template:
                self.assertEqual(graph["avm_scale_first_frame"]["inputs"]["crop"], "center")
    def test_provider_uses_constructor_snapshot_after_environment_changes(self):
        provider = ComfyUIVideoProvider(base_url="http://127.0.0.1:1", megapixels=0.7, steps=20, lora="")
        with patch.dict(os.environ, {"H3_MEGAPIXELS": "0.98", "H3_STEPS": "4", "H3_LORA": "wrong.safetensors"}):
            graph = provider._build_workflow("English.", 10, "image.png")
        self.assertEqual(graph["119"]["inputs"]["megapixels"], 0.7)
        self.assertEqual(graph["9"]["inputs"]["steps"], 20)
        self.assertNotIn("121", graph)

    def test_standard_model_reconnects_every_consumer_in_both_workflows(self):
        for template in ("h3_i2v_api.json", "h3_r2v_api.json"):
            provider = ComfyUIVideoProvider(base_url="http://local", workflow_path=str(Path(__file__).parents[2] / "comfyui" / template), steps=20, lora="")
            graph = provider._build_workflow("English.", 5, "image.png")
            self.assertEqual(graph["16"]["inputs"]["model"], ["6", 0])
            self.assertEqual(graph["9"]["inputs"]["model"], ["6", 0])
            for node in graph.values():
                for value in node.get("inputs", {}).values():
                    if isinstance(value, list) and len(value) == 2:
                        self.assertIn(str(value[0]), graph, "Workflow contains a dangling node link")

    def test_reference_precision_is_frozen_and_continuation_keeps_its_guide(self):
        provider = ComfyUIVideoProvider(base_url="http://local", workflow_path=str(Path(__file__).parents[2] / "comfyui" / "h3_r2v_api.json"), steps=20, lora="", ref_image_size="max")
        with patch.dict(os.environ, {"H3_REF_IMAGE_SIZE": "match"}):
            graph = provider._build_workflow("New action.", 5, "hero.png", guide_name="last.png")
        self.assertEqual(graph["104"]["inputs"]["ref_image_size"], "max")
        guide = next(key for key, node in graph.items() if node["class_type"] == "MiniMaxH3AddGuide")
        self.assertEqual(graph[guide]["inputs"]["frame_idx"], 0)
        self.assertEqual(graph["16"]["inputs"]["conditioning"], [guide, 0])
        self.assertEqual(graph["16"]["inputs"]["model"], ["6", 0])
        with self.assertRaises(ValueError):
            ComfyUIVideoProvider(base_url="http://local", ref_image_size="unknown")

    def test_streams_publish_only_on_success_and_preserve_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.mp4"
            path.write_bytes(b"existing")
            def fail():
                yield b"partial"
                raise OSError("connection lost")
            with self.assertRaises(OSError):
                _save_video_stream(Mock(iter_content=Mock(return_value=fail())), str(path))
            self.assertEqual(path.read_bytes(), b"existing")
            with self.assertRaises(RuntimeError):
                _save_video_stream(Mock(iter_content=Mock(return_value=[])), str(path))
            _save_video_stream(Mock(iter_content=Mock(return_value=[b"complete"])), str(path))
            self.assertEqual(path.read_bytes(), b"complete")
            self.assertEqual(list(Path(directory).glob("*.part")), [])

    def test_resolution_policy_rejects_nonfinite_and_out_of_range_values(self):
        for value in (float("nan"), float("inf"), 0, 1.5):
            with self.assertRaises(ValueError):
                resolve_resolution(value, {"video_backend": "comfyui"})


if __name__ == "__main__":
    unittest.main()
