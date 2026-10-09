import subprocess
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import imageio_ffmpeg
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from server.canvas_export import CanvasExportClip, clip_export_name, pack_clips, register_canvas_export_routes, render_export, resolve_clips


class CanvasExportTests(unittest.TestCase):
    def wait_export(self, client, job_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = client.get(f'/api/tasks/test/canvas-export/{job_id}').json()
            if result['status'] != 'running':
                return result
            time.sleep(.01)
        self.fail('export did not finish')

    def test_package_contains_only_selected_originals_with_ordered_chinese_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            segments = root / 'segments'
            segments.mkdir()
            contents = {'first.mp4': b'first original bytes', 'second.mp4': b'second original bytes', 'excluded.mp4': b'excluded'}
            for name, data in contents.items(): (segments / name).write_bytes(data)
            app = FastAPI()
            ffmpeg = unittest.mock.Mock(side_effect=AssertionError('packaging must not transcode'))
            register_canvas_export_routes(app, lambda _: {'project': {'title': '归家'}}, lambda _: directory, ffmpeg)
            client = TestClient(app)
            response = client.post('/api/tasks/test/canvas-export/clips', json={'clips': [
                {'file': 'second.mp4', 'order': 4, 'title': '04 · 灶边的奶奶'},
                {'file': 'first.mp4', 'order': 1, 'title': '01 · 夜行的侧脸'},
            ]})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['mode'], 'clips')
            job = self.wait_export(client, response.json()['job_id'])
            self.assertEqual(job['status'], 'succeeded')
            self.assertEqual((job['done'], job['total']), (2, 2))
            self.assertEqual(job['download_name'], '归家_镜头素材.zip')
            with zipfile.ZipFile(root / 'export' / job['url'].split('/')[-1]) as archive:
                self.assertEqual(archive.namelist(), ['01_夜行的侧脸.mp4', '04_灶边的奶奶.mp4'])
                self.assertEqual(archive.read('01_夜行的侧脸.mp4'), contents['first.mp4'])
                self.assertEqual(archive.read('04_灶边的奶奶.mp4'), contents['second.mp4'])
            self.assertEqual({name: (segments / name).read_bytes() for name in contents}, contents)
            self.assertEqual(client.get(f"/api/tasks/foreign/canvas-export/{job['job_id']}").status_code, 404)
            ffmpeg.assert_not_called()
            self.assertFalse(list((root / 'export').glob('.canvas-package-*')))

    def test_package_validates_selection_and_project_local_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'segments').mkdir()
            (Path(directory) / 'segments' / 'a.mp4').touch()
            app = FastAPI()
            register_canvas_export_routes(app, lambda _: {}, lambda _: directory, lambda: 'ffmpeg')
            client = TestClient(app)
            bad_selections = [[], [{'file': 'a.mp4', 'order': 0}], [{'file': 'a.mp4', 'order': 301}],
                              [{'file': 'a.mp4', 'order': 1}, {'file': 'a.mp4', 'order': 1}],
                              [{'file': '../a.mp4', 'order': 1}], [{'file': 'other/a.mp4', 'order': 1}]]
            for clips in bad_selections:
                with self.subTest(clips=clips):
                    self.assertEqual(client.post('/api/tasks/test/canvas-export/clips', json={'clips': clips}).status_code, 422)
            self.assertEqual(client.post('/api/tasks/test/canvas-export/clips', json={'clips': [{'file': 'missing.mp4', 'order': 1}]}).status_code, 404)
            self.assertFalse((Path(directory) / 'export').exists())

    def test_package_names_are_safe_unique_and_sort_for_three_digit_sequences(self):
        clips = [CanvasExportClip(file='a.mp4', order=order, title='01 · ../家:门\\窗?*<>|\x00') for order in [1, 12, 100]]
        names = [clip_export_name(clip, 3) for clip in clips]
        self.assertEqual([name.split('_', 1)[0] for name in sorted(names)], ['001', '012', '100'])
        for name in names:
            self.assertEqual(Path(name).name, name)
            self.assertFalse(any(char in name for char in '\\/:*?"<>|\x00'))
            self.assertIn('家', name)

    def test_failed_package_does_not_publish_partial_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'a.mp4'
            source.write_bytes(b'original')
            output = root / 'clips.zip'
            output.write_bytes(b'previous complete archive')
            with patch('server.canvas_export.zipfile.ZipFile.write', side_effect=OSError('disk full')):
                with self.assertRaises(OSError): pack_clips([source], [CanvasExportClip(file='a.mp4', order=1)], output, lambda _: None)
            self.assertEqual(output.read_bytes(), b'previous complete archive')
            self.assertEqual(source.read_bytes(), b'original')
            self.assertFalse(list(root.glob('.canvas-package-*')))

    def test_old_merge_request_still_runs_and_reports_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'segments').mkdir()
            (Path(directory) / 'segments' / 'a.mp4').touch()
            app = FastAPI()
            register_canvas_export_routes(app, lambda _: {}, lambda _: directory, lambda: 'ffmpeg')
            client = TestClient(app)
            with patch('server.canvas_export.render_export', side_effect=lambda clips, output, aspect, ffmpeg, progress: output.write_bytes(b'merged')) as render:
                response = client.post('/api/tasks/test/canvas-export', json={'files': ['a.mp4'], 'aspect': '1:1'})
                self.assertEqual(response.status_code, 200)
                job = self.wait_export(client, response.json()['job_id'])
                self.assertEqual(job['status'], 'succeeded')
                self.assertEqual(job['mode'], 'merge')
                self.assertTrue(job['url'].endswith('.mp4'))
                self.assertEqual(render.call_args.args[2], '1:1')

    def test_package_and_merge_share_one_active_job_per_project(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'segments').mkdir()
            (Path(directory) / 'segments' / 'a.mp4').touch()
            started, release = threading.Event(), threading.Event()
            def held_package(clips, metadata, output, progress):
                started.set()
                if not release.wait(3): raise RuntimeError('test worker timeout')
                output.write_bytes(b'package')
            app = FastAPI()
            register_canvas_export_routes(app, lambda _: {}, lambda _: directory, lambda: 'ffmpeg')
            client = TestClient(app)
            with patch('server.canvas_export.pack_clips', side_effect=held_package):
                response = client.post('/api/tasks/test/canvas-export/clips', json={'clips': [{'file': 'a.mp4', 'order': 1}]})
                try:
                    self.assertTrue(started.wait(1))
                    self.assertEqual(client.post('/api/tasks/test/canvas-export', json={'files': ['a.mp4']}).status_code, 409)
                finally:
                    release.set()
                    self.assertEqual(self.wait_export(client, response.json()['job_id'])['status'], 'succeeded')

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
