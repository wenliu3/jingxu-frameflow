"""Muse contracts and real media publishing; no account/browser/model calls."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from muse_video_provider import MuseVideoProvider
from schemas import Project


class MuseContracts(unittest.TestCase):
    def setUp(self):
        import server.app as app
        self.app = app
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image = self.root / "photo.png"
        self.image.write_bytes(b"image")
        self.project = Project.from_dict({"title": "Muse", "assets": [
            {"kind": "image", "name": "First", "images": [str(self.image)]},
            {"kind": "image", "name": "Second", "images": [str(self.image)]},
        ]})
        self.cfg = {**app.SERVICE_DEFAULTS, "video_backend": "muse", "video_workflow": "ref2va", "comfyui_url": ""}
        self.bridge = Mock(status=Mock(return_value={"connected": True, "busy": False}),
                           call=Mock(return_value={"connected": True}))
        self.provider = Mock()
        self.runners = []
        from types import SimpleNamespace
        fake_thread = lambda **kw: self.runners.append(kw["target"]) or Mock()
        for name, value in (
            ("_load_service_config", Mock(side_effect=lambda: self.cfg.copy())),
            ("_task", Mock(return_value={"task_id": "abcdef123456", "status": "succeeded"})),
            ("_reload", Mock(return_value=(self.project, []))), ("_out_dir", Mock(return_value=str(self.root))),
            ("MUSE_BRIDGE", self.bridge), ("_make_video_provider", Mock(return_value=self.provider)),
            ("SEGMENT_JOBS", {}), ("threading", SimpleNamespace(Thread=fake_thread)),
            ("agents.compose_segment_prompt", Mock(side_effect=AssertionError("No text model"))),
        ):
            patcher = patch("server.app." + name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(app.app)
        self.addCleanup(self.client.close)

    def start(self, **kwargs):
        return self.client.post("/api/tasks/abcdef123456/segment/video", json={
            "prompt": "镜头缓缓向前。", "expected_backend": "muse", "expected_workflow": "muse", **kwargs})

    def test_plain_description_and_context_do_not_require_llm_or_images(self):
        response = self.client.post("/api/tasks/abcdef123456/compose", json={"description": "一只熊猫在竹林散步。", "context": "清晨"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("一只熊猫", response.json()["prompt"])
        self.assertIn("清晨", response.json()["prompt"])
        self.assertEqual(response.json()["video_workflow"], "muse")

    def test_multiple_images_are_described_and_queued_without_h3_controls(self):
        response = self.client.post("/api/tasks/abcdef123456/compose", json={"images": ["First", "Second"], "description": "镜头推进"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("参考图 2：Second", response.json()["prompt"])
        started = self.start(frames=["image:0", "image:1"])
        self.assertEqual(started.status_code, 200)
        record = json.loads(next((self.root / "segments").glob("seg_*.json")).read_text(encoding="utf-8"))
        self.assertEqual(record["mode"], "muse")
        for field in ("seed", "megapixels", "model_duration", "video_steps"):
            self.assertIsNone(record.get(field))
        self.assertTrue(all(m["role"] == "selected" for m in record["materials"]))

    def reference_materials(self):
        character_sheet = self.root / "hero_sheet.png"
        prop_sheet = self.root / "sword_sheet.png"
        scene = self.root / "path.png"
        for filename in (character_sheet, prop_sheet, scene):
            filename.write_bytes(b"reference image")
        project = Project.from_dict({"characters": [
            {"name": "Hero", "image_path": str(self.image), "sheet": str(character_sheet)}], "assets": [
            {"kind": "scene", "name": "Path", "images": [str(scene)]},
            {"kind": "prop", "name": "Sword", "sheet": str(prop_sheet), "images": []},
        ]})
        self.project.characters = project.characters
        self.project.assets = project.assets
        return character_sheet, prop_sheet, scene

    def test_character_and_sheet_only_prop_reach_muse_in_composed_reference_order(self):
        references = self.reference_materials()
        response = self.client.post("/api/tasks/abcdef123456/compose", json={
            "characters": ["Hero"], "props": ["Sword"], "scene": "Path",
            "description": "@{character:Hero} carries @{prop:Sword} through @{scene:Path}.",
        })
        self.assertEqual(response.status_code, 200, response.text)
        prompt = response.json()["prompt"]
        for index, name in enumerate(("Hero", "Sword", "Path"), 1):
            self.assertIn(f"参考图 {index}：{name}", prompt)
            self.assertIn(f"参考图 {index}（{name}）", response.json()["video_prompt"])
        started = self.start(prompt=prompt, frames=["scene:0", "prop:1", "character:0"],
                             frame_names={"character:0": "Hero", "prop:1": "Sword", "scene:0": "Path"})
        self.assertEqual(started.status_code, 200, started.text)
        self.provider.last_output_info = {}
        self.provider.generate.side_effect = lambda **kw: Path(kw["out_path"]).write_bytes(b"completed video")
        self.runners[0]()
        args = self.provider.generate.call_args.kwargs
        self.assertEqual(args["ref_image_paths"], [str(p) for p in references])
        self.assertEqual(args["image_path"], str(references[0]))
        self.assertEqual(args["video_prompt"], prompt)
        record = json.loads(next((self.root / "segments").glob("seg_*.json")).read_text(encoding="utf-8"))
        self.assertEqual([m["file"] for m in record["materials"]], [p.name for p in references])
        self.assertEqual([m["name"] for m in record["materials"]], ["Hero", "Sword", "Path"])

    def test_missing_sheet_only_prop_is_named_and_never_submitted(self):
        _, prop_sheet, _ = self.reference_materials()
        prop_sheet.unlink()
        response = self.client.post("/api/tasks/abcdef123456/compose", json={
            "characters": ["Hero"], "props": ["Sword"], "scene": "Path", "description": "Walk."})
        self.assertEqual(response.status_code, 422)
        self.assertIn("Sword", response.json()["detail"])
        started = self.start(frames=["character:0", "prop:1", "scene:0"])
        self.assertEqual(started.status_code, 422)
        self.assertIn("Sword", started.json()["detail"])
        self.assertEqual(self.runners, [])
        self.provider.generate.assert_not_called()
        self.bridge.call.assert_not_called()

    def test_pure_text_can_start_and_unsupported_inputs_are_rejected(self):
        self.assertEqual(self.start().status_code, 200)
        for settings in ({"candidate_count": 2}, {"seed": 10}, {"exact_duration": True},
                         {"megapixels": 0.5}, {"last_frame": "image:0"}):
            self.assertEqual(self.start(**settings).status_code, 422, settings)

    def test_disconnected_and_busy_fail_before_submitting(self):
        self.bridge.status.return_value = {"connected": False}
        self.assertEqual(self.start().status_code, 409)
        self.bridge.status.return_value = {"connected": True, "busy": True}
        self.assertEqual(self.start().status_code, 409)
        self.assertEqual(self.runners, [])

    def test_browser_actions_do_not_save_service_config_or_accept_foreign_origins(self):
        self.assertEqual(self.client.post("/api/muse/open").status_code, 200)
        self.bridge.call.assert_called_once_with("open")
        self.assertEqual(self.client.post("/api/muse/connect", headers={"Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.bridge.call.call_count, 1)
        self.assertEqual(self.cfg["video_backend"], "muse")

    def test_capabilities_match_muse_and_do_not_inherit_ref2va(self):
        response = self.client.get("/api/video-capabilities").json()
        self.assertEqual(response["candidate_counts"], [1])
        self.assertTrue(response["text_to_video"])
        self.assertTrue(response["video_references"])
        self.assertTrue(response["settings_as_requests"])
        self.assertIn("16:9", response["ratios"])
        self.assertIn("720p", response["resolutions"])
        self.assertFalse(response["seed"])

    def test_panel_endpoints_are_local_only_and_do_not_expose_debug_commands(self):
        response = self.client.get("/api/muse/frame")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.bridge.call.assert_called_with("frame")
        payload = {"kind": "text", "frame_id": "fixture", "text": "中文输入"}
        self.assertEqual(self.client.post("/api/muse/input", json=payload).status_code, 200)
        self.bridge.call.assert_called_with("input", payload, timeout=15)
        self.assertEqual(self.client.post("/api/muse/resize", json={"width": 960, "height": 600}).status_code, 200)
        self.assertEqual(self.client.post("/api/muse/window").status_code, 200)
        self.bridge.call.assert_called_with("openWindow")
        calls = self.bridge.call.call_count
        for endpoint, method, data in (("frame", "get", None), ("window", "post", None),
                                       ("resize", "post", {"width": 960, "height": 600}), ("input", "post", payload)):
            response = self.client.request(method, f"/api/muse/{endpoint}", json=data,
                                           headers={"Origin": "https://foreign.example"})
            self.assertEqual(response.status_code, 403)
        for body in ({"kind": "evaluate", "frame_id": "fixture", "text": "alert(1)"},
                     {**payload, "url": "https://example.com"}, {**payload, "text": "a" * 10001},
                     {"kind": "click", "frame_id": "fixture", "x": -1, "y": 0}):
            self.assertEqual(self.client.post("/api/muse/input", json=body).status_code, 422)
        self.assertEqual(self.client.post("/api/muse/resize", json={"width": 99999, "height": 600}).status_code, 422)
        self.assertEqual(self.bridge.call.call_count, calls)

    def test_recover_resumes_original_candidate_and_never_creates_a_generation(self):
        started = self.start()
        job_id = started.json()["job_id"]
        job = self.app.SEGMENT_JOBS[job_id]
        job["status"] = "interrupted"
        job["results"][0]["status"] = "interrupted"
        journal = Path(job["results"][0]["out_path"]).with_suffix(".muse.json")
        journal.write_text(json.dumps({"stage": "submitted", "task_id": "muse_112233445566"}), encoding="utf-8")
        self.runners.clear()
        provider = Mock()
        def finish(**kwargs):
            self.assertTrue(kwargs["recover_only"])
            self.assertEqual(kwargs["video_prompt"], "")
            self.assertEqual(kwargs["image_path"], "")
            Path(kwargs["out_path"]).write_bytes(b"video")
        provider.generate.side_effect = finish
        provider.last_output_info = {"actual_duration": 10.03, "width": 1920, "height": 1080}
        with patch("muse_video_provider.MuseVideoProvider", return_value=provider):
            response = self.client.post(f"/api/segment-jobs/{job_id}/muse/recover?task_id=abcdef123456")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.runners), 1)
        self.runners[0]()
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["results"][0]["actual_duration"], 10.03)
        self.assertEqual(len(self.app.SEGMENT_JOBS), 1)
        self.assertEqual(self.client.post(f"/api/segment-jobs/{job_id}/muse/recover?task_id=abcdef123456").status_code, 200)
        self.assertEqual(len(self.runners), 1)
        self.assertEqual(self.client.post(f"/api/segment-jobs/{job_id}/muse/recover?task_id=abcdef123456", headers={"Origin": "https://evil.example"}).status_code, 403)

    def test_recover_requires_a_sent_muse_task_and_connection(self):
        job_id = self.start().json()["job_id"]
        job = self.app.SEGMENT_JOBS[job_id]
        job["status"] = "failed"
        route = f"/api/segment-jobs/{job_id}/muse/recover?task_id=abcdef123456"
        self.assertEqual(self.client.post(route).status_code, 409)
        Path(job["results"][0]["out_path"]).with_suffix(".muse.json").write_text(json.dumps({"stage": "draft"}), encoding="utf-8")
        self.assertEqual(self.client.post(route).status_code, 409)

    def test_recover_reopens_only_original_unique_thread_after_restart(self):
        job_id = self.start().json()["job_id"]
        job = self.app.SEGMENT_JOBS[job_id]
        job["status"] = "interrupted"
        journal = Path(job["results"][0]["out_path"]).with_suffix(".muse.json")
        record = {"stage": "submitted", "task_id": "muse_112233445566",
                  "chat_url": "https://muse.ai/thread/024908e9-49c8-40ca-9bfd-bc36c3fdf90e"}
        journal.write_text(json.dumps(record), encoding="utf-8")
        self.bridge.status.return_value = {"connected": False, "busy": False}
        self.bridge.call.return_value = {"connected": True, "busy": False}
        self.runners.clear()
        response = self.client.post(f"/api/segment-jobs/{job_id}/muse/recover?task_id=abcdef123456")
        self.assertEqual(response.status_code, 200)
        self.bridge.call.assert_called_once_with("resumeChat", {"journal_path": str(journal)}, timeout=90)
        self.assertEqual(len(self.runners), 1)
        self.assertEqual(len(self.app.SEGMENT_JOBS), 1)

    def test_recover_never_guesses_root_chat_or_navigates_external_url(self):
        job_id = self.start().json()["job_id"]
        job = self.app.SEGMENT_JOBS[job_id]
        job["status"] = "interrupted"
        journal = Path(job["results"][0]["out_path"]).with_suffix(".muse.json")
        self.bridge.status.return_value = {"connected": False, "busy": False}
        self.runners.clear()
        for url in ("https://muse.ai/", "https://foreign.example/thread/024908e9-49c8-40ca-9bfd-bc36c3fdf90e", "https://muse.ai/thread/024908e9-49c8-40ca-9bfd-bc36c3fdf90e?redirect=foreign"):
            journal.write_text(json.dumps({"stage": "submitted", "task_id": "muse_112233445566", "chat_url": url}), encoding="utf-8")
            self.assertEqual(self.client.post(f"/api/segment-jobs/{job_id}/muse/recover?task_id=abcdef123456").status_code, 409)
        self.bridge.call.assert_not_called()
        self.assertEqual(self.runners, [])

    def import_job(self):
        job_id = self.start().json()["job_id"]
        job = self.app.SEGMENT_JOBS[job_id]
        job["status"] = "interrupted"
        job["results"][0]["status"] = "interrupted"
        journal = Path(job["results"][0]["out_path"]).with_suffix(".muse.json")
        journal.write_text(json.dumps({"stage": "submitted", "task_id": "muse_112233445566", "chat_url": "https://muse.ai/", "error": "取回超时"}), encoding="utf-8")
        self.runners.clear()
        return job, journal, f"/api/segment-jobs/{job_id}/muse/import?task_id=abcdef123456&filename=muse_112233445566.mp4"

    def test_import_download_completes_original_job_without_browser_or_generation(self):
        import imageio_ffmpeg
        import subprocess
        job, journal, route = self.import_job()
        source = self.root / "download.mp4"
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=64x48:r=24:d=1", "-c:v", "libx264", str(source)], check=True, capture_output=True)
        self.bridge.status.return_value = {"connected": False}
        response = self.client.post(route, content=source.read_bytes())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "succeeded")
        self.assertEqual(job["results"][0]["width"], 64)
        self.assertEqual(job["results"][0]["height"], 48)
        self.assertAlmostEqual(job["results"][0]["actual_duration"], 1, places=1)
        self.assertEqual(job["results"][0]["muse_task_id"], "muse_112233445566")
        self.assertEqual(len(self.app.SEGMENT_JOBS), 1)
        self.assertEqual(self.runners, [])
        self.bridge.call.assert_not_called()
        updated = json.loads(journal.read_text(encoding="utf-8"))
        self.assertEqual(updated["stage"], "downloaded")
        self.assertEqual(updated["result_source"], "local_download")
        self.assertNotIn("error", updated)
        self.assertEqual(self.client.post(route, content=b"ignored duplicate").json()["status"], "succeeded")

    def test_import_rejects_wrong_task_filename_foreign_origin_and_empty_file(self):
        job, journal, route = self.import_job()
        self.assertEqual(self.client.post(route.replace("112233445566.mp4", "ffffffffffff.mp4"), content=b"video").status_code, 422)
        self.assertEqual(self.client.post(route, content=b"video", headers={"Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.client.post(route, content=b"").status_code, 400)
        self.assertEqual(job["status"], "interrupted")
        job["status"] = "running"
        self.assertEqual(self.client.post(route, content=b"video").status_code, 409)
        self.bridge.call.assert_not_called()

    def test_import_bad_media_does_not_publish_or_complete_the_version(self):
        job, journal, route = self.import_job()
        response = self.client.post(route.replace(".mp4", "%20(1).mp4"), content=b"<html>not a video</html>")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "failed")
        self.assertFalse(Path(job["results"][0]["out_path"]).exists())
        self.assertEqual(json.loads(journal.read_text(encoding="utf-8"))["stage"], "submitted")
        self.bridge.call.assert_not_called()

    def test_import_persistence_failure_stops_before_publishing(self):
        job, journal, route = self.import_job()
        with patch("server.app.save_candidate", side_effect=OSError("disk full")):
            response = self.client.post(route, content=b"video")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(job["status"], "interrupted")
        self.assertFalse(Path(job["results"][0]["out_path"]).exists())
        self.assertEqual(self.runners, [])


class MusePublishing(unittest.TestCase):
    def test_provider_forwards_video_files_and_requested_specs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image, video = root / "hero.png", root / "clip.mp4"
            image.write_bytes(b"image"); video.write_bytes(b"video")
            bridge = Mock(call=Mock(return_value={"task_id": "muse_test"}))
            provider = MuseVideoProvider(bridge=bridge)
            with patch("muse_video_provider.publish_video", return_value={"width": 1280, "height": 720}):
                provider.generate(str(image), "Use reference video 1.", 5, str(root / "result.mp4"),
                                  ref_video_paths=[str(video)], ratio="16:9", resolution="720p")
            payload = bridge.call.call_args.args[1]
            self.assertEqual(payload["videos"], [str(video.resolve())])
            self.assertEqual(payload["images"], [str(image.resolve())])
            self.assertEqual(payload["ratio"], "16:9")
            self.assertEqual(payload["resolution"], "720p")
            with self.assertRaises(ValueError):
                provider.generate("", "Reference", 5, str(root / "result.mp4"), ref_video_paths=[str(root / "missing.mp4")])

    def test_recover_downloads_using_the_original_journal_without_images_or_prompt(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "seg_abcdef123456.mp4"
            target.with_suffix(".muse.json").write_text('{"task_id":"muse_112233445566"}', encoding="utf-8")
            bridge = Mock(call=Mock(return_value={"task_id": "muse_112233445566"}))
            provider = MuseVideoProvider(bridge=bridge)
            with patch("muse_video_provider.publish_video", return_value={"actual_duration": 10.03}):
                provider.recover(str(target))
            command, payload = bridge.call.call_args.args
            self.assertEqual(command, "recover")
            self.assertEqual(payload["journal_path"], str(target.with_suffix(".muse.json")))
            self.assertNotIn("prompt", payload)
            self.assertNotIn("images", payload)
            self.assertEqual(provider.last_output_info["actual_duration"], 10.03)
    def test_worker_multiplexes_readonly_status_without_opening_a_browser(self):
        from concurrent.futures import ThreadPoolExecutor
        from server.muse_bridge import MuseBridge
        bridge = MuseBridge(Path(__file__).resolve().parents[2])
        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda _: bridge.call("status", timeout=10), range(8)))
            self.assertTrue(all(not result["browser_open"] for result in results))
            self.assertEqual(bridge.pending, {})
        finally:
            bridge.close()
            if bridge.process:
                bridge.process.wait(timeout=10)

    def test_real_video_is_validated_published_and_measured(self):
        import imageio_ffmpeg
        import subprocess
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.mp4"
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=64x64:r=24:d=1", "-c:v", "libx264", str(source)], check=True, capture_output=True)
            def generate(command, payload, timeout):
                Path(payload["raw_path"]).write_bytes(source.read_bytes())
                return {"task_id": "muse_test"}
            provider = MuseVideoProvider(bridge=Mock(call=Mock(side_effect=generate)))
            target = root / "seg_abcdef123456.mp4"
            provider.generate("", "小熊散步", 5, str(target), generate_audio=False)
            self.assertGreater(target.stat().st_size, 100)
            self.assertEqual(provider.last_output_info["muse_task_id"], "muse_test")
            self.assertEqual(provider.last_output_info["width"], 64)

    def test_html_download_never_becomes_successful_video(self):
        with tempfile.TemporaryDirectory() as folder:
            def generate(command, payload, timeout):
                Path(payload["raw_path"]).write_text("<html>Login required</html>")
                return {}
            provider = MuseVideoProvider(bridge=Mock(call=Mock(side_effect=generate)))
            target = Path(folder) / "output.mp4"
            with self.assertRaises(RuntimeError):
                provider.generate("", "小熊散步", 5, str(target))
            self.assertFalse(target.exists())
