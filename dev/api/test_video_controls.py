"""Real local media checks; no models, user files or network services."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import imageio_ffmpeg
from video_controls import h3_frames, image_size, output_size, publish_video


class VideoControlsTests(unittest.TestCase):
    def test_output_dimensions_preserve_ratio_inside_resolution_envelope(self):
        expected = {"16:9": (1280, 720), "9:16": (720, 1280), "1:1": (720, 720), "4:3": (960, 720), "3:4": (720, 960), "21:9": (1280, 548)}
        for ratio, size in expected.items():
            self.assertEqual(output_size(ratio, "720p", "unused", 0.5), size)
        self.assertIsNone(output_size("auto", "custom", "unused", 0.7))
        with patch("video_controls.image_size", return_value=(1000, 2000)):
            self.assertEqual(output_size("auto", "720p", "image", 0.7), (640, 1280))
        for duration in (1, 1.5, 2, 3, 4, 4.5, 6.5, 15):
            self.assertEqual(h3_frames(duration) % 17, 5)
            self.assertLessEqual(abs(h3_frames(duration) / 24 - duration), 17 / 48)

    def test_exact_duration_resize_and_audio_switch_use_real_video(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root / "source.mp4"
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=24", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "1.5", "-c:v", "libx264", "-c:a", "aac", str(source)], check=True)
            self.assertEqual(image_size(str(source)), (160, 90))
            for audio, length in ((True, 4.5), (False, 1.0)):
                output = root / f"result-{audio}.mp4"
                result = publish_video(str(source), str(output), width=240, height=320, duration=length, generate_audio=audio)
                self.assertEqual((result["width"], result["height"]), (240, 320))
                self.assertAlmostEqual(result["actual_duration"], length, delta=1 / 24)
                self.assertEqual(result["has_audio"], audio)
            self.assertTrue(source.is_file())
            self.assertEqual(list(root.glob(".video-publish-*")), [])

    def test_failed_conversion_keeps_previous_output_and_cleans_temporary_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); output = root / "old.mp4"; output.write_bytes(b"previous version")
            with self.assertRaises(RuntimeError):
                publish_video(str(root / "missing.mp4"), str(output), duration=6.5)
            self.assertEqual(output.read_bytes(), b"previous version")
            self.assertEqual(list(root.glob(".video-publish-*")), [])
