"""Offline library summaries: only temporary files, no model calls."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from server.project_summary import project_summary, _workflow


class ProjectSummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.task = {"task_id": "abcdef123456", "flow": "blocks", "status": "succeeded",
                     "stage_state": "draft", "project": {"characters": [], "assets": []},
                     "shots": [{"shot_id": 1}], "blocks": []}

    def summary(self):
        return project_summary(self.task, str(self.root))

    def test_empty_draft_is_not_a_completed_video(self):
        self.assertEqual(self.summary(), {"shots": 0, "material_count": 0, "video_count": 0,
                                         "cover_url": "", "stage_state": "draft"})

    def test_canvas_is_authoritative_and_corrupt_canvas_falls_back(self):
        self.task["blocks"] = [{}, {}, {}]
        path = self.root / "workflow.json"
        path.write_text(json.dumps({"nodes": [{"type": "shot"}, {"type": "video"}, {"type": "shot"}]}))
        self.assertEqual(self.summary()["shots"], 2)
        path.write_text(json.dumps({"nodes": []}))
        self.assertEqual(self.summary()["shots"], 0)
        path.write_text("{broken")
        self.assertEqual(self.summary()["shots"], 3)

    def test_unchanged_canvas_is_not_reparsed(self):
        path = self.root / "workflow.json"
        path.write_text('{"nodes": [{"type": "shot"}]}')
        self.assertEqual(self.summary()["shots"], 1)
        with patch("server.project_summary.json.loads", side_effect=AssertionError("unexpected reparse")):
            self.assertEqual(self.summary()["shots"], 1)

    def test_video_count_skips_empty_outputs_and_non_video_files(self):
        for folder in ("segments", "videos"):
            path = self.root / folder
            path.mkdir()
            (path / "ok.MP4").write_bytes(b"video")
            (path / "empty.mp4").touch()
            (path / "metadata.json").write_text("{}")
            (path / "folder.mp4").mkdir()
        self.assertEqual(self.summary()["video_count"], 2)

    def test_cover_uses_existing_scene_and_encodes_filename(self):
        path = self.root / "assets"
        path.mkdir()
        (path / "scene 空间.png").write_bytes(b"image")
        self.task["project"] = {"characters": [{"name": "hero"}], "assets": [
            {"kind": "audio", "images": ["sample.mp3"]},
            {"kind": "scene", "images": ["C:\\old\\assets\\scene 空间.png"]}]}
        result = self.summary()
        self.assertEqual(result["material_count"], 3)
        self.assertTrue(result["cover_url"].startswith("/files/abcdef123456/assets/scene%20%E7%A9%BA%E9%97%B4.png?v="))
        (path / "scene 空间.png").unlink()
        self.assertEqual(self.summary()["cover_url"], "")

    def test_legacy_shot_counts_and_previews(self):
        self.task["flow"] = "shots"
        (self.root / "images").mkdir()
        (self.root / "images" / "shot_01.png").write_bytes(b"image")
        self.task["shots"][0]["image_path"] = "shot_01.png"
        self.assertEqual(self.summary()["shots"], 1)
        self.assertIn("/images/shot_01.png", self.summary()["cover_url"])

    def test_task_list_exposes_summary_and_active_generation(self):
        from fastapi.testclient import TestClient
        import server.app as app_module
        self.task["created_at"] = "2026-10-04T00:00:00Z"
        (self.root / "workflow.json").write_text('{"nodes": [{"type": "shot"}]}')
        with patch.object(app_module, "TASKS", {self.task["task_id"]: self.task}), \
             patch.object(app_module, "_out_dir", return_value=str(self.root)), \
             patch.object(app_module, "SEGMENT_JOBS", {"job": {"task_id": self.task["task_id"], "status": "running"}}), \
             patch.object(app_module, "VIDEO_JOBS", {}), patch.object(app_module, "BATCH_JOBS", {}), \
             TestClient(app_module.app) as client:
            response = client.get("/api/tasks")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["shots"], 1)
        self.assertEqual(response.json()[0]["status"], "running")
        self.assertEqual(response.json()[0]["stage_state"], "draft")


if __name__ == "__main__":
    unittest.main()
