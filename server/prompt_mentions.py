"""Resolve editor material mentions against the actual generation reference plan."""
import re
from urllib.parse import unquote

MENTION = re.compile(r"@\{(character|prop|scene|image|video):([^{}]+)\}")


def resolve_mentions(text, plan, *, manual=False, ref_mode=True, videos=(), muse=False):
    slots = {(s.role, s.label): s for s in plan.slots if s.kind == "image"}
    image_numbers = {(s.role, s.label): i for i, s in enumerate(
        (s for s in plan.slots if s.kind == "image"), 1)}

    def replace(match):
        kind, encoded = match.groups()
        name = unquote(encoded, errors="strict")
        if kind == "video":
            node_id, _, label = name.partition("|")
            visual = [v for v in videos if v.get("clip_path")]
            found = next(((i, v) for i, v in enumerate(visual) if v["source_node"] == node_id), None)
            if found is None:
                raise ValueError(f"引用的视频「{label or node_id}」未连接为视频参考或未就绪，请重新选择")
            index, source = found
            tag = f"参考视频 {index + 1}" if muse else f"<Video {index + 1}>"
            return f"{source['label']}（{tag}）" if not manual or muse else tag
        slot = slots.get((kind, name))
        if slot is None:
            raise ValueError(f"引用的素材「{name}」未连接或未就绪，请重新选择或删除这处引用")
        if muse:
            return f"参考图 {image_numbers[(kind, name)]}（{name}）"
        if not ref_mode:
            return "the subject in the input image" if manual else name
        tag = slot.subject or slot.tag
        return tag if manual else f"{name}（{tag}）"

    return MENTION.sub(replace, text)
