"""Resolve editor material mentions against the actual generation reference plan."""
import re
from urllib.parse import unquote

MENTION = re.compile(r"@\{(character|prop|scene|image):([^{}]+)\}")


def resolve_mentions(text, plan, *, manual=False, ref_mode=True):
    slots = {(s.role, s.label): s for s in plan.slots if s.kind == "image"}

    def replace(match):
        kind, encoded = match.groups()
        name = unquote(encoded, errors="strict")
        slot = slots.get((kind, name))
        if slot is None:
            raise ValueError(f"引用的素材「{name}」未连接或未就绪，请重新选择或删除这处引用")
        if not ref_mode:
            return "the subject in the input image" if manual else name
        tag = slot.subject or slot.tag
        return tag if manual else f"{name}（{tag}）"

    return MENTION.sub(replace, text)
