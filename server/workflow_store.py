"""Per-project storyboard canvas, independent of generation services."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from typing import Any, Callable, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Position(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    x: float = Field(0, ge=-100000, le=100000)
    y: float = Field(0, ge=-100000, le=100000)


class Viewport(Position):
    zoom: float = Field(0.8, ge=0.15, le=2)


class CanvasNode(Position):
    id: str = Field(min_length=1, max_length=100)
    type: Literal["material", "shot", "video", "note", "footage"]
    data: dict[str, Any] = Field(default_factory=dict)


class CanvasEdge(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    source: str = Field(min_length=1, max_length=100)
    target: str = Field(min_length=1, max_length=100)
    usage: Literal["text", "reference", "continue"] = "text"
    sourceVersionId: str = Field("", max_length=100)
    start: float | None = Field(None, ge=0, le=3600, allow_inf_nan=False)
    end: float | None = Field(None, gt=0, le=3600, allow_inf_nan=False)
    tailSeconds: float = Field(3, ge=0.25, le=15, allow_inf_nan=False)
    motionReference: bool = True


class WorkflowDocument(BaseModel):
    version: Literal[1] = 1
    revision: int = Field(0, ge=0)
    nodes: list[CanvasNode] = Field(default_factory=list, max_length=300)
    edges: list[CanvasEdge] = Field(default_factory=list, max_length=1200)
    viewport: Viewport = Field(default_factory=Viewport)

    @model_validator(mode="after")
    def validate_graph(self):
        ids = {n.id for n in self.nodes}
        if len(ids) != len(self.nodes):
            raise ValueError("节点编号不能重复")
        if len({e.id for e in self.edges}) != len(self.edges):
            raise ValueError("连线编号不能重复")
        pairs = set()
        kinds = {n.id: n.type for n in self.nodes}
        outgoing = {nid: [] for nid in ids}
        indegree = {nid: 0 for nid in ids}
        for edge in self.edges:
            if edge.source not in ids or edge.target not in ids:
                raise ValueError("连线引用了不存在的节点")
            if (edge.source, edge.target) in pairs:
                raise ValueError("相同节点之间不能重复连线")
            pairs.add((edge.source, edge.target))
            allowed = (kinds[edge.source], kinds[edge.target]) in {
                ("material", "shot"), ("note", "shot"), ("footage", "shot"),
                ("shot", "shot"), ("shot", "video"),
            }
            if edge.source == edge.target or not allowed:
                raise ValueError("这两种节点不能连接")
            outgoing[edge.source].append(edge.target)
            indegree[edge.target] += 1
        for edge in self.edges:
            if edge.usage != "text" and (kinds[edge.source] not in {"shot", "footage"} or kinds[edge.target] != "shot"):
                raise ValueError("只有视频卡之间可以设置视频参考用途")
            if edge.start is not None and edge.end is not None and edge.start >= edge.end:
                raise ValueError("参考结束时间必须晚于开始时间")
        for nid in ids:
            if sum(e.target == nid and e.usage == "continue" for e in self.edges) > 1:
                raise ValueError("一段视频只能有一个续拍起点")
            if sum(e.target == nid and e.usage != "text" for e in self.edges) > 3:
                raise ValueError("最多三个视频来源")
        queue = [nid for nid in ids if not indegree[nid]]
        visited = 0
        while queue:
            nid = queue.pop()
            visited += 1
            for target in outgoing[nid]:
                indegree[target] -= 1
                if indegree[target] == 0:
                    queue.append(target)
        if visited != len(ids):
            raise ValueError("连线不能形成循环")
        if len(self.model_dump_json().encode("utf-8")) > 2_000_000:
            raise ValueError("画布内容过大，请减少节点或文字")
        return self


def register_workflow_routes(
    app: FastAPI, task_lookup: Callable, out_dir: Callable,
) -> None:
    write_lock = threading.Lock()

    def read(task_id: str) -> WorkflowDocument:
        path = os.path.join(out_dir(task_id), "workflow.json")
        if not os.path.isfile(path):
            return WorkflowDocument()
        try:
            with open(path, encoding="utf-8") as fh:
                return WorkflowDocument.model_validate(json.load(fh))
        except (OSError, ValueError) as exc:
            raise HTTPException(500, "画布文件读取失败，请保留 workflow.json 并检查文件") from exc

    @app.get("/api/tasks/{task_id}/workflow")
    def get_workflow(task_id: str):
        task_lookup(task_id)
        with write_lock:
            return read(task_id)

    @app.put("/api/tasks/{task_id}/workflow")
    def save_workflow(task_id: str, body: WorkflowDocument):
        task_lookup(task_id)
        with write_lock:
            current = read(task_id)
            if body.revision != current.revision:
                raise HTTPException(409, "画布已在其他页面更新，请重新载入后再编辑")
            body.revision += 1
            directory = out_dir(task_id)
            temp_path = ""
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=directory,
                    prefix=".workflow-", suffix=".tmp", delete=False,
                ) as fh:
                    temp_path = fh.name
                    fh.write(body.model_dump_json(indent=2))
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(temp_path, os.path.join(directory, "workflow.json"))
            except OSError as exc:
                raise HTTPException(500, "画布保存失败，请检查作品目录是否可写") from exc
            finally:
                if temp_path and os.path.isfile(temp_path):
                    os.remove(temp_path)
        return {"ok": True, "revision": body.revision}
