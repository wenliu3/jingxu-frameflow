"""ComfyUI result selection and interrupted media transfer regressions."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

from video_provider import ComfyUIVideoProvider


def response(payload=None, status=200):
    result = requests.Response()
    result.status_code = status
    result.url = "http://comfy/test"
    result._content = json.dumps(payload or {}).encode()
    result.raw = Mock()
    return result


class ComfyUITransferTests(unittest.TestCase):
    def setUp(self):
        self.provider = ComfyUIVideoProvider(base_url="http://comfy", poll_interval=0, timeout_per_shot=30)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def history(self, outputs):
        return response({"pid": {"status": {"completed": True, "status_str": "success"}, "outputs": outputs}})

    def test_continuation_downloads_saved_video_instead_of_loader_input(self):
        history = self.history({
            "avm_video_0": {"images": [{"filename": "ref_clip.mp4", "type": "input", "subfolder": ""}]},
            "preview": {"images": [{"filename": "preview.png", "type": "output"}]},
            "temporary": {"videos": [{"filename": "preview.mp4", "type": "temp"}]},
            "92": {"images": [{"filename": "avm_00029_.mp4", "type": "output", "subfolder": "video"}]},
        })
        with patch("video_provider.requests.get", return_value=history):
            self.assertEqual(self.provider._wait("pid"), ("avm_00029_.mp4", "video"))

    def test_legacy_video_combine_without_type_is_still_supported(self):
        with patch("video_provider.requests.get", return_value=self.history({"60": {"gifs": [{"filename": "result.mp4"}]}})):
            self.assertEqual(self.provider._wait("pid"), ("result.mp4", ""))

    def test_inputs_and_still_images_cannot_be_published_as_a_video(self):
        history = self.history({
            "loader": {"images": [{"filename": "ref.mp4", "type": "input"}]},
            "preview": {"images": [{"filename": "frame.png", "type": "output"}]},
        })
        with patch("video_provider.requests.get", return_value=history):
            with self.assertRaisesRegex(RuntimeError, "没有保存"):
                self.provider._wait("pid")

    def test_upload_reopens_entire_file_after_gateway_failure(self):
        path = self.root / "ref.mp4"
        path.write_bytes(b"entire reference")
        bodies = []

        def upload(url, **kwargs):
            bodies.append(kwargs["files"]["image"][1].read())
            self.assertEqual(kwargs["data"]["overwrite"], "true")
            return response(status=502) if len(bodies) == 1 else response({"name": "ref.mp4", "subfolder": "refs"})

        with patch("video_provider.requests.post", side_effect=upload), patch("video_provider.time.sleep") as sleep:
            self.assertEqual(self.provider._upload(str(path)), "refs/ref.mp4")
            sleep.assert_called_once()
        self.assertEqual(bodies, [b"entire reference", b"entire reference"])

    def test_upload_rejects_permanent_request_error_without_retry(self):
        path = self.root / "ref.png"
        path.write_bytes(b"reference")
        with patch("video_provider.requests.post", return_value=response(status=400)) as post, patch("video_provider.time.sleep") as sleep:
            with self.assertRaises(requests.HTTPError):
                self.provider._upload(str(path))
            post.assert_called_once()
            sleep.assert_not_called()

    def test_broken_download_preserves_old_file_until_complete_retry(self):
        path = self.root / "result.mp4"
        path.write_bytes(b"old video")

        def interrupted():
            yield b"partial new video"
            raise requests.exceptions.ChunkedEncodingError("connection lost")

        first = response()
        first.iter_content = Mock(return_value=interrupted())
        second = response()
        second.iter_content = Mock(return_value=[b"complete new video"])
        attempts = []

        def download(url, **kwargs):
            self.assertEqual(path.read_bytes(), b"old video")
            self.assertEqual(kwargs["params"], {"filename": "result.mp4", "subfolder": "video", "type": "output"})
            attempts.append(url)
            return first if len(attempts) == 1 else second

        with patch("video_provider.requests.get", side_effect=download), patch("video_provider.requests.post") as post, patch("video_provider.time.sleep"):
            self.provider._download("result.mp4", "video", str(path))
            post.assert_not_called()
        self.assertEqual(len(attempts), 2)
        self.assertEqual(path.read_bytes(), b"complete new video")
        self.assertEqual(list(self.root.glob("*.part")), [])

    def test_download_gateway_failure_retries_same_result_without_resubmission(self):
        success = response()
        success.iter_content = Mock(return_value=[b"completed video"])
        with patch("video_provider.requests.get", side_effect=[response(status=502), success]) as get, patch("video_provider.requests.post") as post, patch("video_provider.time.sleep"):
            self.provider._download("result.mp4", "video", str(self.root / "result.mp4"))
            self.assertEqual(get.call_count, 2)
            post.assert_not_called()

    def test_download_not_found_is_not_retried(self):
        with patch("video_provider.requests.get", return_value=response(status=404)) as get, patch("video_provider.time.sleep") as sleep:
            with self.assertRaises(requests.HTTPError):
                self.provider._download("missing.mp4", "video", str(self.root / "result.mp4"))
            get.assert_called_once()
            sleep.assert_not_called()

    def test_persistent_outage_stops_after_bounded_retries(self):
        with patch("video_provider.requests.get", side_effect=requests.ConnectionError("offline")) as get, patch("video_provider.time.sleep") as sleep:
            with self.assertRaises(requests.ConnectionError):
                self.provider._download("result.mp4", "video", str(self.root / "result.mp4"))
            self.assertEqual(get.call_count, 6)
            self.assertEqual(sleep.call_count, 5)
        self.assertFalse((self.root / "result.mp4").exists())

    def test_reference_capability_check_recovers_after_gateway_failure(self):
        schema = {"MiniMaxH3ReferenceToVideo": {"input": {"required": {}}}}
        with patch("video_provider.requests.get", side_effect=[response(status=502), response(schema)]) as get, patch("video_provider.time.sleep"):
            self.provider._preflight_video_inputs(False, False)
            self.assertEqual(get.call_count, 2)

    def test_interrupted_remote_generation_is_not_retried(self):
        history = response({"pid": {"status": {"status_str": "error", "completed": False, "messages": [["execution_interrupted", {"node_id": "14"}]]}}})
        with patch("video_provider.requests.get", return_value=history) as get, patch("video_provider.requests.post") as post:
            with self.assertRaisesRegex(RuntimeError, "execution_interrupted"):
                self.provider._wait("pid")
            get.assert_called_once()
            post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
