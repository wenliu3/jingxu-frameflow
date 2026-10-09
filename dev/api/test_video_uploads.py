"""Real local upload conversion and reference/continuation; fake generation only."""
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import imageio_ffmpeg

from dev.api import test_video_contract as base
from server.workflow_store import WorkflowDocument


class VideoUploadTests(unittest.TestCase):
    setUpClass = classmethod(base.VideoContractTests.setUpClass.__func__)

    def setUp(self):
        base.VideoContractTests.setUp(self)
        self.cfg.update(video_workflow="ref2va")
        self.url = "/api/tasks/abcdef123456/video-uploads"
        self.source = self.root / "original.mov"
        subprocess.run([
            imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-v", "error", "-f", "lavfi", "-i",
            "color=c=blue:s=160x96:r=24:d=1", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-c:v", "libx264", "-c:a", "aac", "-shortest", str(self.source),
        ], check=True, capture_output=True)

    def upload(self, **kwargs):
        return self.client.post(self.url, params={"filename": "../../参考片.mov"},
            content=self.source.read_bytes(), headers={"Content-Type": "application/octet-stream"}, **kwargs)

    def test_uploaded_video_is_playable_saved_and_usable_for_reference_and_continuation(self):
        response = self.upload()
        self.assertEqual(response.status_code, 200, response.text)
        uploaded = response.json()
        self.assertEqual(uploaded["original_filename"], "参考片.mov")
        self.assertEqual((uploaded["width"], uploaded["height"]), (160, 96))
        self.assertAlmostEqual(uploaded["actual_duration"], 1, delta=0.1)
        self.assertTrue(uploaded["has_audio"])
        directory = self.root / "video_uploads"
        self.assertEqual(len(list(directory.iterdir())), 3)
        self.assertTrue((directory / uploaded["file"]).is_file())
        reader = imageio_ffmpeg.read_frames(str(directory / uploaded["file"]))
        try:
            self.assertEqual(next(reader)["size"], (160, 96))
            self.assertGreater(len(next(reader)), 0)
        finally:
            reader.close()
        canvas = WorkflowDocument.model_validate({"nodes": [
            {"id": "uploaded", "type": "footage", "data": {"title": "参考片", "versions": [uploaded]}},
            {"id": "target", "type": "shot", "data": {"title": "新镜头"}},
        ], "edges": [{"id": "edge", "source": "uploaded", "target": "target", "usage": "reference"}]})
        (self.root / "workflow.json").write_text(canvas.model_dump_json(), encoding="utf-8")
        source = {"source_node": "uploaded", "version_id": uploaded["id"], "file": uploaded["file"]}
        for usage in ("reference", "continue"):
            composed = self.client.post("/api/tasks/abcdef123456/compose", json={
                "video_prompt": "[Shot 1] The scene slowly changes.", "duration": 1,
                "video_sources": [{**source, "usage": usage}],
            })
            self.assertEqual(composed.status_code, 200, composed.text)
            self.assertEqual(composed.json()["video_sources"][0]["file"], uploaded["file"])
            response = self.client.post("/api/tasks/abcdef123456/segment/video", json={
                "prompt": composed.json()["prompt"], "duration": 1,
                "video_sources": [{**source, "usage": usage}],
            })
            self.assertEqual(response.status_code, 200, response.text)
            self.runners[-1]()
            self.assertTrue(Path(self.calls[-1]["ref_video_paths"][0]).is_file())
            if usage == "continue":
                self.assertTrue(Path(self.calls[-1]["guide_frame_path"]).is_file())
        uploaded_manifest = directory / Path(uploaded["file"]).with_suffix(".json")
        uploaded_manifest.unlink()
        rejected = self.client.post("/api/tasks/abcdef123456/compose", json={
            "video_prompt": "A scene.", "video_sources": [{**source, "usage": "reference"}],
        })
        self.assertEqual(rejected.status_code, 422)

    def test_invalid_empty_oversize_and_unplayable_uploads_leave_no_files(self):
        for filename, body, status in (("bad.txt", b"data", 422), ("empty.mp4", b"", 400), ("bad.mp4", b"invalid", 422)):
            with self.subTest(filename=filename):
                response = self.client.post(self.url, params={"filename": filename}, content=body)
                self.assertEqual(response.status_code, status, response.text)
        with patch("server.video_uploads.MAX_VIDEO_BYTES", 10):
            self.assertEqual(self.client.post(self.url, params={"filename": "large.mp4"}, content=b"x" * 11).status_code, 413)
        self.assertEqual(list((self.root / "video_uploads").iterdir()), [])
