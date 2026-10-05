import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import imageio_ffmpeg
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from server.canvas_export import register_canvas_export_routes, render_export, resolve_clips


class CanvasExportTests(unittest.TestCase):
    def test_paths_reject_traversal_symlinks_and_missing_clips(self):
        with tempfile.TemporaryDirectory() as directory:
            segment = Path(directory) / 'segments'
            segment.mkdir()
            (segment / 'valid.mp4').touch()
            self.assertEqual(resolve_clips(directory, ['valid.mp4']), [segment / 'valid.mp4'])
            for value in ['../valid.mp4', 'nested/valid.mp4', 'C:\\outside.mp4', 'missing.mp4', "bad'file.mp4"]:
                with self.assertRaises(HTTPException): resolve_clips(directory, [value])

    def test_api_rejects_invalid_aspect_empty_and_foreign_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            app = FastAPI()
            register_canvas_export_routes(app, lambda _: {}, lambda _: directory, imageio_ffmpeg.get_ffmpeg_exe)
            client = TestClient(app)
            self.assertEqual(client.post('/api/tasks/test/canvas-export', json={'files': []}).status_code, 422)
            self.assertEqual(client.post('/api/tasks/test/canvas-export', json={'files': ['a.mp4'], 'aspect': '4:3'}).status_code, 422)
            self.assertEqual(client.get('/api/tasks/test/canvas-export/unknown').status_code, 404)

    def test_real_export_normalizes_sizes_and_adds_silence_without_changing_sources(self):
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clips = [root / 'silent.mp4', root / 'audio.mp4']
            commands = [
                [ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=red:s=80x64:r=24:d=0.5', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(clips[0])],
                [ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=64x96:r=30:d=0.5', '-f', 'lavfi', '-i', 'sine=frequency=400:duration=0.5', '-c:v', 'libx264', '-c:a', 'aac', '-pix_fmt', 'yuv420p', '-shortest', str(clips[1])],
            ]
            for command in commands: subprocess.run(command, check=True, capture_output=True, timeout=30)
            original = [c.read_bytes() for c in clips]
            progress = []
            render_export(clips, root / 'final.mp4', '1:1', ffmpeg, progress.append)
            reader = imageio_ffmpeg.read_frames(str(root / 'final.mp4'))
            try: metadata = next(reader)
            finally: reader.close()
            self.assertEqual(metadata['size'], (720, 720))
            self.assertEqual(metadata['fps'], 24)
            self.assertTrue(metadata.get('audio_codec'))
            self.assertGreaterEqual(metadata['duration'], .9)
            self.assertEqual([c.read_bytes() for c in clips], original)
            self.assertEqual(progress, [1, 2])
            self.assertFalse(list(root.glob('.canvas-export-*')))

    def test_failed_export_cleans_temporary_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch('imageio_ffmpeg.read_frames', side_effect=ValueError('invalid video')):
                with self.assertRaises(ValueError): render_export([root / 'bad.mp4'], root / 'final.mp4', '16:9', 'ffmpeg', lambda _: None)
            self.assertFalse(list(root.glob('.canvas-export-*')))
            self.assertFalse((root / 'final.mp4').exists())


if __name__ == '__main__': unittest.main()
