"""Offline canvas persistence and integrity checks; all files are temporary."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from server.workflow_store import register_workflow_routes


class WorkflowStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="frameflow-workflow-test-")
        self.directory = Path(self.temp.name)
        self.client = self.new_client()
        self.url = "/api/tasks/abcdef123456/workflow"
        self.document = {
            "version": 1, "revision": 0,
            "nodes": [
                {"id": "material", "type": "material", "x": 20, "y": 30, "data": {"materialName": "角色"}},
                {"id": "shot", "type": "shot", "x": 400, "y": 30, "data": {"title": "第一镜"}},
                {"id": "output", "type": "video", "x": 800, "y": 30, "data": {}},
            ],
            "edges": [{"id": "edge", "source": "material", "target": "shot"}],
            "viewport": {"x": 10, "y": 20, "zoom": 0.8},
        }

    def new_client(self):
        app = FastAPI()
        def lookup(task_id):
            if task_id != "abcdef123456":
                raise HTTPException(404, "不存在")
            return {}
        register_workflow_routes(app, lookup, lambda _: str(self.directory))
        return TestClient(app)

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_new_project_has_empty_canvas(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["nodes"], [])
        self.assertEqual(response.json()["revision"], 0)

    def test_saved_canvas_survives_service_recreation(self):
        self.assertEqual(self.client.put(self.url, json=self.document).json()["revision"], 1)
        with self.new_client() as restarted:
            restored = restarted.get(self.url).json()
        self.assertEqual(restored["nodes"], self.document["nodes"])
        self.assertEqual(restored["viewport"], self.document["viewport"])

    def test_stale_editor_cannot_overwrite_newer_changes(self):
        self.client.put(self.url, json=self.document)
        self.document["nodes"][1]["data"]["title"] = "旧页面"
        self.assertEqual(self.client.put(self.url, json=self.document).status_code, 409)
        self.assertEqual(self.client.get(self.url).json()["nodes"][1]["data"]["title"], "第一镜")

    def test_group_members_and_names_survive_recreation_with_reference_edges(self):
        for node in self.document["nodes"][:2]:
            node["data"].update(groupId="reference-group", groupTitle="场景与动作参考")
        self.assertEqual(self.client.put(self.url, json=self.document).status_code, 200)
        with self.new_client() as restarted:
            restored = restarted.get(self.url).json()
        self.assertEqual(restored["nodes"], self.document["nodes"])
        self.assertEqual(restored["edges"][0]["source"], "material")
        self.assertEqual(restored["edges"][0]["target"], "shot")

    def test_unified_video_versions_and_job_survive_service_recreation(self):
        self.document["nodes"] = self.document["nodes"][:2]
        data = self.document["nodes"][1]["data"]
        data.update({
            "status": "running", "jobId": "ongoing", "activeVersionId": "old",
            "versions": [
                {"id": "old", "status": "succeeded", "url": "/files/abcdef123456/segments/old.mp4", "file": "old.mp4"},
                {"id": "new", "status": "running", "jobId": "ongoing", "description": "新版描述", "duration": 5},
            ],
        })
        self.assertEqual(self.client.put(self.url, json=self.document).status_code, 200)
        with self.new_client() as restarted:
            restored = restarted.get(self.url).json()
        self.assertEqual(restored["nodes"][1]["data"], data)
        self.assertEqual([{k: e[k] for k in ("id", "source", "target")} for e in restored["edges"]], self.document["edges"])
        self.assertEqual(restored["edges"][0]["usage"], "text")

    def test_failed_atomic_replace_preserves_previous_canvas(self):
        self.client.put(self.url, json=self.document)
        self.document["revision"] = 1
        self.document["nodes"][1]["data"]["title"] = "未完成写入"
        with patch("server.workflow_store.os.replace", side_effect=OSError("locked")):
            self.assertEqual(self.client.put(self.url, json=self.document).status_code, 500)
        self.assertEqual(json.loads((self.directory / "workflow.json").read_text(encoding="utf-8"))["nodes"][1]["data"]["title"], "第一镜")
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_dangling_and_duplicate_connections_are_rejected(self):
        self.document["edges"][0]["target"] = "missing"
        self.assertEqual(self.client.put(self.url, json=self.document).status_code, 422)
        self.document["edges"][0]["target"] = "shot"
        self.document["edges"].append({"id": "second", "source": "material", "target": "shot"})
        self.assertEqual(self.client.put(self.url, json=self.document).status_code, 422)

    def test_cycles_and_invalid_node_types_are_rejected(self):
        self.document["nodes"].append({"id": "shot2", "type": "shot", "x": 0, "y": 0, "data": {}})
        self.document["edges"].extend([
            {"id": "a", "source": "shot", "target": "shot2"},
            {"id": "b", "source": "shot2", "target": "shot"},
        ])
        self.assertEqual(self.client.put(self.url, json=self.document).status_code, 422)
        self.document["edges"] = [{"id": "e", "source": "output", "target": "material"}]
        self.assertEqual(self.client.put(self.url, json=self.document).status_code, 422)

    def test_invalid_coordinates_and_zoom_are_rejected(self):
        self.document["nodes"][0]["x"] = 200000
        self.assertEqual(self.client.put(self.url, json=self.document).status_code, 422)
        self.document["nodes"][0]["x"] = 0
        self.document["viewport"]["zoom"] = 0
        self.assertEqual(self.client.put(self.url, json=self.document).status_code, 422)

    def test_missing_project_is_not_created_by_saving(self):
        self.assertEqual(self.client.put("/api/tasks/missing/workflow", json=self.document).status_code, 404)
        self.assertFalse((self.directory / "workflow.json").exists())

    def test_corrupt_file_is_preserved_and_reported(self):
        path = self.directory / "workflow.json"
        path.write_text("broken", encoding="utf-8")
        self.assertEqual(self.client.get(self.url).status_code, 500)
        self.assertEqual(self.client.put(self.url, json=self.document).status_code, 500)
        self.assertEqual(path.read_text(encoding="utf-8"), "broken")


if __name__ == "__main__":
    unittest.main()
