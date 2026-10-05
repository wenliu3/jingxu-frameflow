"""Small, read-only summaries for the project library; no generation calls."""
from functools import lru_cache
import json
from pathlib import Path
from urllib.parse import quote


@lru_cache(maxsize=256)
def _workflow(path: str, modified: int, size: int):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        nodes = value.get("nodes") if isinstance(value, dict) else None
        if not isinstance(nodes, list):
            return None
        return sum(1 for node in nodes if isinstance(node, dict) and node.get("type") == "shot")
    except (OSError, ValueError):
        return None


def project_summary(task: dict, directory: str) -> dict:
    root = Path(directory)
    project = task.get("project") or {}
    shots = task.get("shots") or []
    blocks = task.get("blocks") or []
    count = len(blocks) if task.get("flow") == "blocks" else len(shots)
    try:
        stat = (root / "workflow.json").stat()
        saved_count = _workflow(str(root / "workflow.json"), stat.st_mtime_ns, stat.st_size)
        if saved_count is not None:
            count = saved_count
    except OSError:
        pass

    videos = 0
    for folder in ("segments", "videos"):
        try:
            videos += sum(1 for file in (root / folder).iterdir()
                          if file.suffix.lower() == ".mp4" and file.is_file() and file.stat().st_size > 0)
        except OSError:
            pass

    def cover(folder, value):
        # Persisted paths may come from a different machine. Only serve a real
        # image inside this project's expected folder, including symlink checks.
        if not value:
            return ""
        name = str(value).replace("\\", "/").rsplit("/", 1)[-1]
        file = root / folder / name
        try:
            file.resolve().relative_to(root.resolve())
            if file.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".avif"} and file.is_file() and file.stat().st_size:
                return f"/files/{quote(task['task_id'], safe='')}/{folder}/{quote(name, safe='')}?v={file.stat().st_mtime_ns}"
        except (OSError, ValueError):
            pass
        return ""

    image = ""
    for item in shots + blocks:
        image = cover("images", item.get("image_path"))
        if image:
            break
    assets = project.get("assets") or []
    characters = project.get("characters") or []
    for item in sorted(assets, key=lambda a: a.get("kind") != "scene"):
        if image:
            break
        if item.get("kind") == "audio":
            continue
        for value in [*(item.get("images") or []), item.get("sheet")]:
            image = cover("assets", value)
            if image:
                break
    for item in characters:
        if image:
            break
        image = cover("characters", item.get("sheet")) or cover("characters", item.get("image_path"))

    return {"shots": count, "material_count": len(characters) + len(assets),
            "video_count": videos, "cover_url": image,
            "stage_state": task.get("stage_state", "new")}
