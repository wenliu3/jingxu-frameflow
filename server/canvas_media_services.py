"""Canvas media generation using the same character, scene and prop templates as the library."""
from __future__ import annotations

import copy
import os
import shutil
import uuid

from fastapi import HTTPException

import agents
import pipeline
import tts
from image_provider import create_provider, DEFAULT_NEGATIVE
from schemas import Project
from server.portrait_reference import crop_portrait_reference


def material_kind(body):
    return body.image_mode if body.kind == "image" and body.image_mode != "native" else body.kind


def candidate_outputs(output, count):
    return [output if i == 0 else output + f".candidate-{i + 1}.png" for i in range(count)]


def media_services(lookup, out_dir, lock, persist, snapshot):
    def collection(project, kind):
        return project.setdefault("characters" if kind == "character" else "assets", [])

    def find_entry(entries, body, candidate=0):
        kind = material_kind(body)
        return next((a for a in entries if a.get("canvas_node_id") == body.node_id
                     and a.get("canvas_candidate", 0) == candidate
                     and (kind == "character" or a.get("kind") == kind)), None)

    def new_entry(entries, body, candidate=0):
        kind = material_kind(body)
        base = f"{body.title}_{body.node_id.replace('-', '')[:8]}"
        if candidate:
            base += f"_候选{candidate + 1}"
        name, number = base, 2
        while any(a.get("name") == name for a in entries):
            name = f"{base}_{number}"
            number += 1
        entry = {"name": name, "anchor": "", "images": [],
                 "canvas_node_id": body.node_id, "canvas_candidate": candidate}
        if kind == "character":
            entry.update(image_path="", sheet="")
        else:
            entry["kind"] = kind
        return entry

    def prepare(task_id, body):
        with lock:
            task = lookup(task_id)
            if task["status"] == "running" or not task.get("project"):
                raise HTTPException(409, "请等待作品任务完成后再生成素材")
            kind = material_kind(body)
            entries = collection(task["project"], kind)
            entry = find_entry(entries, body)
            if entry is None:
                entry = new_entry(entries, body)
                entries.append(entry)
                persist(task_id)
            return copy.deepcopy(entry)

    def render(task_id, body, output):
        prompt = body.prompt.strip()
        if body.kind == "audio":
            tts.synth(prompt, body.voice_id, output, settings=body.speech_settings)
            return
        provider = create_provider()
        negative = DEFAULT_NEGATIVE
        ratio = body.ratio
        if body.image_mode == "native":
            if body.style_prompt.strip():
                prompt += "\n画面风格：" + body.style_prompt.strip()
        else:
            with lock:
                project = Project.from_dict(copy.deepcopy(lookup(task_id)["project"]))
            if body.style_prompt.strip():
                project.style = body.style_prompt.strip()
            # Design once for the entire batch so candidates depict the same subject.
            if body.image_mode == "character":
                prompt = agents.compose_portrait_sheet(project, body.title, hint=prompt)["sheet"]
                negative, ratio = pipeline.SHEET_NEGATIVE, "16:9"
            elif body.image_mode == "scene":
                desc = agents.design_asset(project, "scene", body.title, hint=prompt)
                prompt = pipeline._scene_prompt(project.style, desc)
                negative += ", person, people, human, human figure, crowd, silhouette"
            else:
                prompt = agents.compose_prop_sheet(project, body.title, hint=prompt)["sheet"]
                negative += ", person, people, human, hand, multiple different objects"
                ratio = "16:9"
        for i, path in enumerate(candidate_outputs(output, body.count)):
            seed = pipeline._stable_seed(body.request_id if i == 0 else f"{body.request_id}:{i}")
            provider.generate(prompt, path, negative_prompt=negative, size=pipeline.SIZE_TABLE[ratio], seed=seed)
            if body.image_mode == "character":
                crop_portrait_reference(path, path + ".portrait.png")

    def publish(task_id, body, output):
        with lock:
            kind = material_kind(body)
            entries = collection(lookup(task_id)["project"], kind)
            entry = find_entry(entries, body)
            if entry is None:
                raise HTTPException(409, "素材已被移除，成品保存在作品的 media_jobs 文件夹")
            folder = "characters" if kind == "character" else "assets"
            directory = os.path.join(out_dir(task_id), folder)
            os.makedirs(directory, exist_ok=True)
            updated = copy.deepcopy(entries)
            copies, results = [], []
            for i, source in enumerate(candidate_outputs(output, body.count)):
                if not os.path.isfile(source) or not os.path.getsize(source):
                    raise RuntimeError(f"第 {i + 1} 张成品文件缺失，已保留原有素材")
                candidate = find_entry(updated, body, i)
                if candidate is None:
                    candidate = new_entry(updated, body, i)
                    updated.append(candidate)
                safe = pipeline._safe_char_name(candidate["name"])
                if body.kind == "image":
                    old_files = list(candidate.get("images", []))
                    if candidate.get("sheet") and candidate["sheet"] not in old_files:
                        old_files.append(candidate["sheet"])
                    snapshot(task_id, kind, safe, old_files, candidate.get("version", ""))
                # New filenames keep every old candidate intact if any part of the batch fails.
                if kind == "character":
                    portrait = source + ".portrait.png"
                    if not os.path.isfile(portrait) or not os.path.getsize(portrait):
                        raise RuntimeError("人物设定图未能提取有效的单人参考图，已保留原有素材")
                    portrait_target = os.path.join(directory, f"{safe}_{body.request_id}_portrait.png")
                    filename = f"{safe}_{body.request_id}_sheet.png"
                    copies.append((portrait, portrait_target))
                else:
                    prefix = {"image": "img", "scene": "scene", "prop": "prop", "audio": "snd"}[kind]
                    filename = f"{prefix}_{safe}_{body.request_id}{'_sheet' if kind == 'prop' else ''}.{'mp3' if kind == 'audio' else 'png'}"
                target = os.path.join(directory, filename)
                copies.append((source, target))
                if kind == "character":
                    candidate.update(images=[portrait_target], image_path=portrait_target, sheet=target)
                elif kind == "prop":
                    candidate.update(images=[], sheet=target)
                else:
                    candidate["images"] = [target]
                candidate.update(version=uuid.uuid4().hex[:6], canvas_input=body.model_dump())
                candidate.pop("stale", None)
                results.append({"asset_name": candidate["name"], "kind": kind, "file": filename,
                                "version": candidate["version"], "index": updated.index(candidate)})
            staged = []
            try:
                for source, target in copies:
                    temporary = target + f".{body.request_id}.tmp"
                    staged.append((temporary, target))
                    shutil.copyfile(source, temporary)
                for temporary, target in staged:
                    os.replace(temporary, target)
            finally:
                for temporary, _ in staged:
                    if os.path.isfile(temporary):
                        os.remove(temporary)
            previous = list(entries)
            entries[:] = updated
            try:
                persist(task_id)
            except Exception:
                entries[:] = previous
                raise
            return {**results[0], "results": results}

    return prepare, render, publish
