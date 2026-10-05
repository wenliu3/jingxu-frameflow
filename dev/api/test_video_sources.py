"""Offline source ownership, extraction, prompt and ComfyUI wiring acceptance."""
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch

import imageio_ffmpeg
from PIL import Image
from fastapi import HTTPException

from dev.api import test_video_contract as base
from server.video_contracts import VideoSource
from server.video_sources import resolve_video_sources
from server.workflow_store import WorkflowDocument
from video_provider import ComfyUIVideoProvider


class VideoSourceTests(unittest.TestCase):
    setUpClass = classmethod(base.VideoContractTests.setUpClass.__func__)

    def setUp(self):
        base.VideoContractTests.setUp(self)
        self.cfg.update(video_workflow="ref2va")
        Image.new("RGB", (160, 96), "green").save(self.image)
        self.file = "seg_012345abcdef.mp4"
        (self.root / "segments").mkdir()
        self.path = self.root / "segments" / self.file
        self.make_video(self.path)
        self.canvas = {"nodes": [{"id": "origin", "type": "shot", "data": {"title": "First video", "description": "changed draft",
            "versions": [{"id": "selected", "status": "succeeded", "file": self.file, "description": "original action"}]}}]}
        self.save_canvas()
        self.source = {"source_node": "origin", "version_id": "selected", "file": self.file, "usage": "continue"}

    def save_canvas(self):
        (self.root / "workflow.json").write_text(json.dumps(self.canvas), encoding="utf-8")

    def make_video(self, path, color=None):
        # Different beginning/ending colors catch the dangerous first-frames crop.
        expression = f"color=c={color}:s=160x96:r=24:d=4" if color else "color=c=red:s=160x96:r=24:d=2[r];color=c=blue:s=160x96:r=24:d=2[b];[r][b]concat=n=2:v=1:a=0"
        flag = "-i" if color else "-filter_complex"
        command = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-v", "error"]
        if color:
            command += ["-f", "lavfi"]
        subprocess.run(command + [flag, expression, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True, capture_output=True)

    def resolve(self, **changes):
        return resolve_video_sources(self.root, [VideoSource(**{**self.source, **changes})], 5, "ref2va")[0]

    def test_tail_and_video_batch_use_actual_ending_and_content_cache(self):
        result = self.resolve(tail_seconds=1)
        self.assertEqual(result["frames"] % 17, 5)
        self.assertEqual(result["description"], "original action")
        pixel = Image.open(result["tail_path"]).getpixel((20, 20))
        self.assertGreater(pixel[2], 230)
        reader = imageio_ffmpeg.read_frames(result["clip_path"])
        meta = next(reader)
        frames = list(reader)
        self.assertEqual(meta["fps"], 24)
        self.assertEqual(len(frames), result["frames"])
        self.assertFalse(meta.get("audio_codec"))
        self.assertGreater(frames[0][2], 230)
        self.assertEqual(self.resolve(tail_seconds=1)["clip_path"], result["clip_path"])
        self.make_video(self.path, "green")
        self.assertNotEqual(self.resolve(tail_seconds=1)["expected_sha256"], result["expected_sha256"])
        with self.assertRaises(HTTPException) as rejected:
            self.resolve(expected_sha256=result["expected_sha256"])
        self.assertEqual(rejected.exception.status_code, 409)

    def test_ownership_failed_version_missing_and_invalid_range_are_rejected(self):
        for changes in ({"source_node": "foreign"}, {"version_id": "not-selected"}, {"start": 3, "end": 2}, {"end": 8}):
            with self.subTest(changes=changes), self.assertRaises(HTTPException):
                self.resolve(**changes)
        self.path.with_suffix(".json").write_text('{"status":"failed"}', encoding="utf-8")
        with self.assertRaises(HTTPException):
            self.resolve()
        self.path.unlink()
        with self.assertRaises(HTTPException):
            self.resolve()

    def test_corrupt_video_and_wrong_workflow_fail_before_provider(self):
        self.path.write_bytes(b"not a video")
        response = self.client.post(self.url, json={"prompt": "New action.", "video_sources": [self.source]})
        self.assertEqual(response.status_code, 422)
        self.factory.assert_not_called()
        self.cfg["video_workflow"] = "i2v"
        response = self.client.post(self.url, json={"prompt": "New action.", "video_sources": [self.source]})
        self.assertEqual(response.status_code, 422)

    def test_compose_start_and_all_candidates_share_pinned_visual_input(self):
        composed = self.client.post("/api/tasks/abcdef123456/compose", json={"video_prompt": "The subject slowly walks forward.", "duration": 5, "video_sources": [self.source]})
        self.assertEqual(composed.status_code, 200, composed.text)
        data = composed.json()
        self.assertIn("<Video 1>", data["prompt"])
        self.assertNotIn("<Picture 1>", data["prompt"])
        self.assertIn("video continuation", data["prompt"])
        self.assertIn("Opening frame is guided", data["prompt"])
        snapshot = [{k: v for k, v in data["video_sources"][0].items() if k in VideoSource.model_fields}]
        response = self.client.post(self.url, json={"prompt": data["prompt"], "video_sources": snapshot, "duration": 5, "candidate_count": 4})
        self.assertEqual(response.status_code, 200, response.text)
        self.canvas["nodes"][0]["data"]["versions"][0]["file"] = "seg_ffffffffffff.mp4"
        self.save_canvas()
        self.runners[0]()
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(len({c["guide_frame_path"] for c in self.calls}), 1)
        self.assertEqual(len({tuple(c["ref_video_paths"]) for c in self.calls}), 1)
        self.assertTrue(all(c["ref_image_paths"] == [] for c in self.calls))
        records = [json.loads(p.read_text(encoding="utf-8")) for p in (self.root / "segments").glob("seg_*_*.json")]
        self.assertEqual(len(records), 4)
        self.assertTrue(all(r["video_sources"][0]["file"] == self.file for r in records))
        self.writer.assert_not_called()

    def test_tail_only_guide_does_not_invent_video_or_picture_labels(self):
        composed = self.client.post("/api/tasks/abcdef123456/compose", json={"video_prompt": "The subject looks up.", "duration": 5,
            "video_sources": [{**self.source, "motion_reference": False}]})
        self.assertEqual(composed.status_code, 200, composed.text)
        prompt = composed.json()["prompt"]
        self.assertNotIn("<Video", prompt)
        self.assertNotIn("<Picture", prompt)
        self.assertIn("Opening frame is guided", prompt)

    def test_changed_source_after_compose_and_multiple_origins_are_rejected(self):
        snapshot = self.resolve()
        self.make_video(self.path, "green")
        response = self.client.post(self.url, json={"prompt": "New action.", "video_sources": [{**self.source, "expected_sha256": snapshot["expected_sha256"]}]})
        self.assertEqual(response.status_code, 409)
        response = self.client.post(self.url, json={"prompt": "New action.", "video_sources": [self.source, self.source]})
        self.assertEqual(response.status_code, 422)
        self.factory.assert_not_called()

    def test_ai_compose_gets_real_video_tags_and_validated_retention(self):
        self.writer.side_effect = None
        self.writer.return_value = {"video_prompt": "[Shot 1] The person in <Video 1> slowly looks up.",
            "soundscape": "Wind and footsteps.", "retention": {"<Video 1>": "partially_preserved"}}
        response = self.client.post("/api/tasks/abcdef123456/compose", json={"description": "继续抬头", "duration": 5, "video_sources": [self.source]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("<Video 1>", "\n".join(self.writer.call_args.kwargs["material_lines"]))
        self.assertIn("<Video 1>: partially_preserved", response.json()["prompt"])

    def test_running_durable_candidate_cannot_be_used_as_completed_source(self):
        self.path.with_suffix(".json").write_text('{"job_id":"012345abcdef"}', encoding="utf-8")
        jobs = self.root / "segments" / ".jobs"
        jobs.mkdir()
        (jobs / "012345abcdef.json").write_text(json.dumps({"results": [{"file": self.file, "status": "running"}]}), encoding="utf-8")
        with self.assertRaises(HTTPException) as rejected:
            self.resolve()
        self.assertEqual(rejected.exception.status_code, 409)

    def test_different_sources_with_same_range_have_distinct_upload_names(self):
        first = self.resolve()
        other_file = "seg_ffffffffffff.mp4"
        self.make_video(self.root / "segments" / other_file, "green")
        self.canvas["nodes"][0]["data"]["versions"].append({"id": "other", "status": "succeeded", "file": other_file})
        self.save_canvas()
        second = self.resolve(version_id="other", file=other_file)
        self.assertNotEqual(Path(first["clip_path"]).name, Path(second["clip_path"]).name)
        self.assertNotEqual(Path(first["tail_path"]).name, Path(second["tail_path"]).name)

    def test_workflow_usage_persists_and_rejects_conflicting_continuation(self):
        nodes = [{"id": name, "type": "shot", "data": {}, "x": 0, "y": 0} for name in ("a", "b", "c")]
        edges = [{"id": "ab", "source": "a", "target": "c", "usage": "continue", "sourceVersionId": "v2", "tailSeconds": 2}]
        doc = WorkflowDocument(nodes=nodes, edges=edges)
        restored = WorkflowDocument.model_validate_json(doc.model_dump_json())
        self.assertEqual(restored.edges[0].sourceVersionId, "v2")
        with self.assertRaises(ValueError):
            WorkflowDocument(nodes=nodes, edges=edges + [{"id": "bc", "source": "b", "target": "c", "usage": "continue"}])


class VideoWorkflowTests(unittest.TestCase):
    def provider(self):
        return ComfyUIVideoProvider(base_url="http://mock-comfyui", workflow_path="comfyui/h3_r2v_api.json")

    def test_semantic_videos_and_start_guide_have_distinct_correct_connections(self):
        provider = self.provider()
        provider.template["avm_video_0"] = {"class_type": "ExistingNode", "inputs": {}}
        workflow = provider._build_workflow("A subject continues walking.", 5, "hero.png", video_names=["tail.mp4", "other.mp4"], guide_name="last.png")
        h3 = workflow["104"]["inputs"]
        for index in (0, 1):
            components = workflow[h3[f"ref_videos.ref_video_{index}"][0]]
            self.assertEqual(components["class_type"], "GetVideoComponents")
            self.assertEqual(workflow[components["inputs"]["video"][0]]["class_type"], "LoadVideo")
        guide_id = workflow["16"]["inputs"]["conditioning"][0]
        guide = workflow[guide_id]
        self.assertEqual(guide["class_type"], "MiniMaxH3AddGuide")
        self.assertEqual(guide["inputs"]["positive"], ["104", 0])
        self.assertEqual(guide["inputs"]["latent"], ["104", 1])
        self.assertEqual(guide["inputs"]["frame_idx"], 0)
        self.assertEqual(guide["inputs"]["vae"], h3["vae"])
        self.assertEqual(workflow["14"]["inputs"]["latent_image"], ["104", 1])
        self.assertFalse(any(key.startswith("ref_video_audios") for key in h3))
        self.assertEqual(workflow["avm_video_0"]["class_type"], "ExistingNode")

    def test_video_only_has_no_fake_picture_and_cleans_old_reference_slots(self):
        provider = self.provider()
        provider.template["104"]["inputs"]["ref_images.ref_image_8"] = ["100", 0]
        workflow = provider._build_workflow("New action.", 5, "", video_names=["tail.mp4"], guide_name="last.png", dimension_image_name="last.png")
        self.assertFalse(any(key.startswith("ref_images") for key in workflow["104"]["inputs"]))
        self.assertNotIn("<Picture", workflow["104"]["inputs"]["prompt"])
        self.assertEqual(workflow["100"]["inputs"]["image"], "last.png")

    def test_missing_remote_nodes_abort_before_upload_or_submission(self):
        provider = self.provider()
        with patch("video_provider.requests.get", return_value=Mock(json=Mock(return_value={}))), patch.object(provider, "_upload") as upload, patch.object(provider, "_submit") as submit:
            with self.assertRaisesRegex(RuntimeError, "ComfyUI"):
                provider.generate("unused.png", "New action.", 5, "unused.mp4", ref_video_paths=["clip.mp4"], guide_frame_path="tail.png")
            upload.assert_not_called()
            submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
