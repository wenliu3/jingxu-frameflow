"""Persistence fault injection uses temporary projects; no model calls."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from atomic_io import atomic_write_json, atomic_write_text
import pipeline
from schemas import Project, Shot


class AtomicPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_unicode_records_and_preview_remain_readable(self):
        project = Project(title="镜序 · 新作品", logline="一封旧信", style="水彩")
        pipeline._save_outputs(str(self.root), project, [Shot(shot_id=1)])
        pipeline._save_blocks(str(self.root), [{"block_id": 1, "summary": "雨夜"}])
        self.assertEqual(json.loads((self.root / "project.json").read_text(encoding="utf-8"))["title"], project.title)
        self.assertEqual(json.loads((self.root / "shots.json").read_text(encoding="utf-8"))[0]["shot_id"], 1)
        self.assertIn(project.title, (self.root / "preview.html").read_text(encoding="utf-8"))
        self.assertEqual(json.loads((self.root / "blocks.json").read_text(encoding="utf-8"))[0]["summary"], "雨夜")

    def test_write_sync_and_replace_failures_preserve_previous_bytes(self):
        path = self.root / "project.json"
        for operation in ("fsync", "replace"):
            path.write_bytes(b'{"title": "original"}')
            with self.subTest(operation=operation), patch(f"atomic_io.os.{operation}", side_effect=OSError("disk failure")):
                with self.assertRaises(OSError):
                    atomic_write_json(path, {"title": "replacement"})
            self.assertEqual(path.read_bytes(), b'{"title": "original"}')
            self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_failed_first_write_leaves_no_partial_record(self):
        path = self.root / "new.json"
        with patch("atomic_io.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                atomic_write_json(path, {"title": "new"})
        self.assertFalse(path.exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_invalid_json_and_text_do_not_truncate_previous_record(self):
        path = self.root / "record.json"
        path.write_text("previous", encoding="utf-8")
        for write in (lambda: atomic_write_json(path, {"bad": object()}), lambda: atomic_write_text(path, None)):
            with self.assertRaises(TypeError):
                write()
            self.assertEqual(path.read_text(encoding="utf-8"), "previous")
            self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_concurrent_writers_publish_a_complete_record(self):
        path = self.root / "record.json"
        values = [{"index": i, "text": str(i) * 10000} for i in range(16)]
        with ThreadPoolExecutor(max_workers=4) as workers:
            list(workers.map(lambda value: atomic_write_json(path, value), values))
        self.assertIn(json.loads(path.read_text(encoding="utf-8")), values)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_invalid_shots_leave_all_previous_output_files_intact(self):
        project = Project(title="Original", logline="", style="")
        pipeline._save_outputs(str(self.root), project, [])
        before = {file.name: file.read_bytes() for file in self.root.iterdir()}
        project.title = "Replacement"
        shot = Shot(shot_id=1)
        shot.scene_desc = object()
        with self.assertRaises(TypeError):
            pipeline._save_outputs(str(self.root), project, [shot])
        self.assertEqual({file.name: file.read_bytes() for file in self.root.iterdir()}, before)

    def test_project_and_block_publish_failures_preserve_valid_files(self):
        project = Project(title="Original", logline="", style="")
        pipeline._save_outputs(str(self.root), project, [])
        pipeline._save_blocks(str(self.root), [{"block_id": 1}])
        before = {file.name: file.read_bytes() for file in self.root.iterdir()}
        with patch("atomic_io.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                pipeline._save_outputs(str(self.root), project, [Shot(shot_id=1)])
            with self.assertRaises(OSError):
                pipeline._save_blocks(str(self.root), [])
        self.assertEqual({file.name: file.read_bytes() for file in self.root.iterdir()}, before)

    def test_task_metadata_failure_keeps_previous_record_and_is_logged(self):
        import server.app as module
        task_id = "abcdef123456"
        path = self.root / "task.json"
        path.write_bytes(b'{"status": "succeeded"}')
        with patch.object(module, "TASKS", {task_id: {"status": "failed"}}), \
             patch.object(module, "_out_dir", return_value=str(self.root)), \
             patch("atomic_io.os.replace", side_effect=OSError("disk full")), \
             self.assertLogs("server.app", level="WARNING"):
            module._save_meta(task_id)
        self.assertEqual(path.read_bytes(), b'{"status": "succeeded"}')
        self.assertEqual(list(self.root.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
