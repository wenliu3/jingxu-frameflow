"""Generation capability and resolution rules shared by HTTP entry points."""
import math


def effective_workflow(cfg):
    backend = cfg.get("video_backend")
    return backend if backend in ("api", "muse") else cfg.get("video_workflow") or "i2v"


def resolve_resolution(override, cfg):
    if cfg.get("video_backend") in ("api", "muse"):
        return None
    value = float(cfg.get("video_megapixels", "0.5") if override is None else override)
    if not math.isfinite(value) or not 0.1 <= value <= 0.98:
        raise ValueError("视频像素预算必须在 0.1 到 0.98MP 之间")
    return value
