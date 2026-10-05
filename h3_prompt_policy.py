"""Local H3 prompt checks; no model, filesystem or HTTP dependencies."""
from dataclasses import dataclass, field
import re

REF_SECTIONS = ('subject_definitions', 'summary', 'retention_analysis',
                'detailed_description', 'overall_soundscape', 'non_diegetic_music')
SECTION_PATTERN = re.compile(r'^\s*(' + '|'.join(REF_SECTIONS) + r')\s*:', re.M)
LABEL_PATTERN = re.compile(r'<(Subject|Picture|Video|Audio)\s+(\d+)>')
VISUAL_RETENTION = {'fully_preserved', 'partially_preserved', 'attribute_transfer', 'weak_reference'}


@dataclass
class PromptAudit:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def visible_prompt(text):
    # Dialogue is user content, not timing or reference syntax to interpret.
    return re.sub(r'<d>.*?</d>', '', text or '', flags=re.S)


def reference_labels(text):
    return {f'<{kind} {int(number)}>' for kind, number in LABEL_PATTERN.findall(visible_prompt(text))}


def reference_errors(text, allowed):
    unknown = reference_labels(text) - set(allowed)
    return [f'引用了未提供的素材编号：{", ".join(sorted(unknown))}'] if unknown else []


def retention_errors(retention, allowed):
    if not isinstance(retention, dict):
        return ['retention 必须是素材标签与保留标记组成的对象']
    if any(tag not in allowed or not isinstance(marker, str) or marker not in VISUAL_RETENTION
           for tag, marker in retention.items()):
        return ['retention 使用了未提供的标签或不支持的视觉保留标记']
    return []


def shot_intent(description, duration):
    """Default to a continuous shot. Infer cuts only from explicit user instructions."""
    text = description or ''
    if re.search(r'一镜到底|不切镜|无切镜|不要切镜|不要切换镜头|单镜头|single[ -](?:take|shot)|one continuous shot|no cuts', text, re.I):
        return 1, []
    numbers = re.findall(r'\[(?:Shot|镜头)\s*(\d+)\]', text, re.I)
    explicit = re.search(r'([\d一二两三四五六七八九十]+)\s*(?:个镜头|颗镜头|镜头|shots\b)', text, re.I)
    cuts = re.findall(r'切到|切换到|切镜|cut(?:s)? to|shot transitions to', text, re.I)
    wanted = max(map(int, numbers), default=1)
    if explicit:
        number = explicit[1]
        chinese = dict(zip('一二两三四五六七八九十', (1, 2, 2, 3, 4, 5, 6, 7, 8, 9, 10)))
        value = int(number) if number.isdigit() else chinese.get(number, 1)
        wanted = max(wanted, value)
    elif cuts:
        wanted = max(wanted, len(cuts) + 1)
    limit = min(2 if duration <= 5 else 3 if duration <= 10 else 4, max(1, int(duration // 2)))
    count = min(max(1, wanted), limit)
    warnings = [f'{duration:g} 秒内请求了 {wanted} 个镜头，编排压缩为 {count} 个，避免切镜过密。'] if wanted > limit else []
    return count, warnings


def audit_body(text, duration=0, *, allowed=None, expected_shots=None):
    audit = PromptAudit()
    text = visible_prompt(text)
    if not text.strip():
        audit.errors.append('detailed_description 是空的')
        return audit
    if SECTION_PATTERN.search(text):
        audit.errors.append('正文内混入六段式字段，请只返回 detailed_description 正文')
    shots = list(re.finditer(r'\[Shot\s+(\d+)\]', text))
    if not shots:
        audit.errors.append('正文里一个 [Shot N] 标记都没有')
    elif [int(m[1]) for m in shots] != list(range(1, len(shots) + 1)):
        audit.errors.append('镜头编号必须从 [Shot 1] 开始连续递增，不能重复或跳号')
    if expected_shots is not None and len(shots) != expected_shots:
        audit.errors.append(f'用户要求 {expected_shots} 个镜头，正文却包含 {len(shots)} 个')
    stamps = []
    for index, shot in enumerate(shots):
        end = shots[index + 1].start() if index + 1 < len(shots) else len(text)
        body = text[shot.end():end].strip()
        if not body:
            audit.errors.append(f'[Shot {shot[1]}] 缺少画面内容')
        stamp = re.match(r'At\s+(\d{2}):(\d{2})\.(\d{3})\s*,', body)
        if int(shot[1]) == 1:
            if re.match(r'At\s+\d', body):
                audit.errors.append('[Shot 1] 不应附带时间戳')
            continue
        if not stamp:
            audit.errors.append(f'[Shot {shot[1]}] 后面缺 At MM:SS.mmm, 时间戳')
            continue
        minutes, seconds, milliseconds = map(int, stamp.groups())
        value = minutes * 60 + seconds + milliseconds / 1000
        if seconds >= 60 or value <= 0 or (duration and value >= duration):
            audit.errors.append(f'[Shot {shot[1]}] 切镜时间必须在视频时长内')
        if stamps and value <= stamps[-1]:
            audit.errors.append('切镜时间必须严格递增')
        stamps.append(value)
    if duration and stamps and max(stamps) > duration - 2 + 1e-6:
        audit.warnings.append(f'最后一个切点在 {max(stamps):.3f}s，距结尾不足 2 秒，收尾可能仓促。')
    if duration and len(shots) > (2 if duration <= 5 else 3 if duration <= 10 else 4):
        audit.warnings.append(f'{duration:g} 秒片有 {len(shots)} 颗镜头，切镜偏碎。')
    if allowed is not None:
        audit.errors.extend(reference_errors(text, allowed))
    return audit


def split_ref_prompt(text):
    matches = list(SECTION_PATTERN.finditer(text or ''))
    if not matches:
        return None
    if [m[1] for m in matches] != list(REF_SECTIONS) or text[:matches[0].start()].strip():
        raise ValueError('Ref2VA 提示词需要按顺序各包含一次完整的六个字段')
    sections = {m[1]: text[m.end():matches[i + 1].start() if i + 1 < len(matches) else len(text)].strip()
                for i, m in enumerate(matches)}
    if any(not value for value in sections.values()):
        raise ValueError('Ref2VA 六个字段都必须有内容，无音乐时写 N/A')
    return sections


def check_ref_prompt(text, duration, allowed):
    sections = split_ref_prompt(text)
    if sections is None:
        raise ValueError('Ref2VA 提示词缺少六段式结构')
    errors = reference_errors(text, allowed)
    audit = audit_body(sections['detailed_description'], duration, allowed=allowed)
    errors.extend(audit.errors)
    if not re.match(r'^\[[^\]]+\]', sections['summary']):
        errors.append('summary 必须以方括号任务类型开头')
    declared_subjects = {tag for tag in reference_labels(sections['subject_definitions']) if tag.startswith('<Subject ')}
    used_subjects = {tag for tag in reference_labels(text) if tag.startswith('<Subject ')}
    if used_subjects - declared_subjects:
        errors.append('使用的主体标签必须先在 subject_definitions 中定义')
    relationships = re.findall(r'^(<(?:Subject|Picture|Video|Audio)\s+\d+>)[^\n:]*:\s*([a-z_]+)\b',
                               sections['retention_analysis'], re.M)
    if not relationships and (allowed or sections['retention_analysis'].strip() != 'N/A'):
        errors.append('retention_analysis 缺少标签与保留标记')
    for tag, marker in relationships:
        permitted = {'fully_copy', 'partially_copy', 'reference', 'weak_reference'} if tag.startswith('<Audio ') else VISUAL_RETENTION
        if marker not in permitted:
            errors.append(f'{tag} 的保留标记不合规：{marker}')
    stated_duration = re.search(r'\b(\d+(?:\.\d+)?)\s*[- ]seconds?\b', sections['summary'])
    if duration and stated_duration and abs(float(stated_duration[1]) - duration) > 0.001:
        errors.append('summary 的视频时长与本次请求不一致')
    if errors:
        raise ValueError('；'.join(dict.fromkeys(errors)))
    return audit.warnings


def prompt_for_reference_images(text, duration, image_count, soundscape='', video_count=0, has_guide=False):
    """Provider guard for full prompts and older CLI callers supplying only a body."""
    sections = split_ref_prompt(text)
    pictures = {f'<Picture {i + 1}>' for i in range(image_count)}
    pictures |= {f'<Video {i + 1}>' for i in range(video_count)}
    if not pictures and not has_guide:
        raise ValueError('Ref2VA 需要图片、视频参考或开头引导')
    if sections is None:
        body = text.strip()
        if not re.search(r'\[Shot\s+\d+\]', body):
            body = '[Shot 1] ' + body
        entries = sorted(pictures, key=lambda tag: int(tag.split()[1][:-1]))
        values = (
            '\n'.join(f'{tag} is a visual reference for the target video.' for tag in entries) or 'N/A',
            f'[reference generation] A {duration:g}-second clip following the target description.',
            '\n'.join(f'{tag}: weak_reference - use its visual appearance where requested.' for tag in entries) or 'N/A',
            body, soundscape.strip() or 'Natural ambience and physical sounds matching the on-screen action.', 'N/A',
        )
        text = '\n\n'.join(f'{name}: {value}' for name, value in zip(REF_SECTIONS, values))
        sections = split_ref_prompt(text)
    subjects = {tag for tag in reference_labels(sections['subject_definitions']) if tag.startswith('<Subject ')}
    check_ref_prompt(text, duration, pictures | subjects)
    return text
