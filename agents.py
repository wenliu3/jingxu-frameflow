"""三个文本 Agent。

职责边界刻意切在「叙事语言」和「视觉语言」之间，而不是按步骤切：

  导演 -> 分镜  ：处理的都是给人看的故事语言
  提示词        ：把故事语言翻译成图像模型能执行的画面语言

这个转换无法省略。「他感到失落」对图像模型是无效输入，
必须变成「低着头，雨水沿下颌滴落，顶光勾出脸的轮廓」。
把它单独成 Agent，将来换图像模型时只需要改这一个文件。
"""

from __future__ import annotations

from schemas import Project, Shot
from llm import chat_json

DIRECTOR_SYSTEM = """你是一位短片导演。用户会给你一句创意或一段剧情，你要把它扩写成可拍摄的故事设定。

硬性要求：
1. 角色外貌必须写成"锚点"形式——固定的、可复述的视觉特征（年龄、发型、发色、服装、显著特征）。
   这些锚点会被原样拼进后续每一个分镜的出图提示词，是跨镜头保持角色一致性的唯一手段。
   所以必须具体、稳定、不随镜头变化。禁止写"帅气""忧郁"这类无法落到画面的词。
2. style 要包含三部分：视觉风格 + 色调 + 参考质感（例如"赛博朋克霓虹，冷蓝与品红对撞，电影胶片颗粒"）。
3. aspect_ratio 从 16:9 / 9:16 / 1:1 中选一个，除非用户在输入里明确指定。
4. 角色数量控制在 1-3 个，多了会出现画面混乱。
5. 每个角色要给 voice（声音设计）：年龄感 + 音色 + 语速语气（例如"清亮的少女音，语速偏快"）。
   它会作为该角色在所有镜头里的配音基调，随声音设计送进视频模型。

只输出 JSON，不要任何解释文字：
{
  "title": "作品标题",
  "logline": "一句话故事",
  "style": "视觉风格 + 色调 + 质感",
  "aspect_ratio": "16:9",
  "characters": [
    {"name": "主角A", "anchor": "28岁亚洲男性，黑色短发，左眉有一道旧疤，深蓝色冲锋衣", "voice": "低沉沉稳的青年男声"}
  ]
}"""


STORYBOARD_SYSTEM = """你是一位分镜师。根据给定的故事设定，拆出指定数量的分镜。

每个分镜必须包含以下字段：
- scene_desc：一句中文画面描述，写清楚「谁、在哪、做什么、什么光线」
- camera：景别，只能从 远景 / 全景 / 中景 / 近景 / 特写 中选一个
- motion：运镜，只能从 固定 / 缓慢推镜 / 缓慢拉镜 / 横移 / 跟随 中选一个
- duration：该镜时长（秒），2 到 5 之间由你按节奏自由决定，小数随意。
  快速反应/动作切口/快节奏剪辑 2-3 秒；常规叙事 3-4 秒；定场、抒情、需要呼吸感 4-5 秒。
  代码会就近吸附到模型帧网格（实际成片约 2.3 / 3 / 3.75 / 4.5 / 5.2 秒这些档位）。
  H3 官方标注的最佳帧区间是 5-15 秒，短镜头稳定性略降——但快节奏本来就需要短镜，
  稳定性优先的定场镜头给长一点即可，不要因此把节奏拉平
- transition：与上一镜的衔接关系，只能填 cut 或 continue。
  cut = 切换了场景或时间（独立镜头，用首帧图起播）；continue = 同一场景的连续动作
  （生成时会自动承接上一镜的最后一帧作为本镜首帧，实现真正的动作衔接）。
  第一个分镜必须填 cut；连续动作被切换打断时宁可用 cut 重新起构图，也不要硬连
- dialogue：该镜的台词或旁白，没有就填空字符串
- character_refs：出现在该镜中的角色 name 列表，没有人物就填空数组

硬性要求：
1. scene_desc 只写能被拍出来的东西，禁止写心理活动。
   错误："他感到一阵失落"      正确："他低着头，雨水顺着下颌滴落"
2. 分镜之间必须有景别和运镜的变化，不允许连续三个分镜用同样的景别。
3. 按时间顺序推进，要有起承转合，最后一个分镜收尾。
4. 如果用户指定了分镜数量，严格输出该数量；未指定时由你决定，
   以把故事讲完整、节奏自然为准，不要注水凑数，也不要压缩成预告片。

只输出 JSON，不要任何解释文字：
{"shots": [{"scene_desc": "...", "camera": "中景", "motion": "缓慢推镜", "duration": 2.5, "transition": "cut", "dialogue": "", "character_refs": ["主角A"]}]}"""


PROMPT_SYSTEM = """你是提示词工程师。每个分镜要写两种提示词，用途完全不同，不要混用。

【一、visual_prompt：给图像模型，产出这一镜的首帧图】
硬性要求：
1. 输出英文，逗号分隔的短语堆叠，不要完整句子。
2. 必须原样包含该镜所有角色的 anchor（翻译成英文），放在提示词前部。
   这是保证角色跨镜头一致的唯一手段。
3. 拼入全局的 style（同样翻译成英文），放在角色锚点之后。
4. 根据 camera 补视角词：特写->close-up shot，近景->medium close-up，
   中景->medium shot，全景->full shot，远景->wide shot。
5. 根据 motion 补静态构图暗示（图像模型只能出静帧）：缓慢推镜->tight framing，
   横移->wide composition 等。
6. negative_prompt 对人物类画面统一使用：
   extra fingers, extra limbs, fused fingers, distorted face, deformed hands,
   malformed limbs, text, watermark, signature, lowres, blurry, jpeg artifacts
   画面中没有人物时可以省略肢体相关的排除项。

【二、video_prompt：配首帧图送 MiniMax H3，驱动画面动起来并生成声音】
H3 是全模态模型，它要的是一份「迷你分镜简报」（shot brief），不是关键词堆，
也不是 Runway 那类极简运动提示词。写成连贯的中文散文，按固定顺序组织：

7. 顺序固定：主体 → 时间轴动作 → 环境反应 → 镜头运动 → 光影 → 落点。
   声音设计单独写进 audio 字段，不要混进 video_prompt。
   这个顺序不能乱，这类模型对开头的内容权重最高。
8. 主体：一句话点名是谁、处于什么状态。只写名字和状态，不要复述外貌——
   发型、服装、疤痕、信物都已经在首帧图里，复述会让模型推翻首帧重新生成，
   表现为闪烁和形变，这是图生视频最常见的失败模式。
9. 时间轴动作：动作按时间顺序展开，用「先……接着……然后……最后……」明确先后。
   H3 对这些时序连接词很敏感，缺了它们，动作会挤成一团分不出节奏。
   有台词就把说话动作写进这一段，台词原文照抄并用引号括起来
   （H3 会连口型和语音一起生成，台词保持中文）。
10. 环境反应：写动作引发的变化——雪被踩得溅起来、雨丝被风吹斜、衣角翻动、
    光在某个表面上移动。是「被引发的反应」，不是重新描述环境长什么样。
11. 镜头运动：只写一个，从 motion 字段翻译。叠加两个以上，模型会两个都做但都不像样。
12. 光影：写光的方向和质量（「顶光从右上方打下」「落地窗的散射光」），
    不要写「氛围感」「电影感」这类情绪词，H3 对具体的物理描述执行得更准。
    也不要写景别和时长——这两样分别由首帧图和生成参数决定，写进去只会干扰。
13. 落点：说明这一镜结束在什么状态（「定格在他低头的侧脸」「镜头停在全景」）。
14. audio（声音设计，单独字段）：H3 画面和音频是一起生成的，这段就是给它看的
    声音简报。全部用正向描述——只说有什么声音，绝不写「不要配乐」「没有音乐」
    这类否定句（模型对否定不敏感，写了照样编）。格式：环境音（具体到声源，
    如「只有海浪拍岸和海风声」）+ 有台词/动作发声时写说话人和音色（结合角色锚点
    与 voice 字段，如「小夏清亮的少女音念出台词」）。默认不想要配乐就一个字
    都不要提音乐，只列环境音。
15. 整段 80 到 200 字。H3 官方建议英文 50-120 词，中文按这个信息量折算；
    超过 200 字不再增加控制，只会引入矛盾。
16. 用中文写——H3 的语言主干是 Qwen3-VL，中文原生支持好，而且你要在界面上读改它。
    镜头运动这类行业术语可以保留英文原词（slow push in / lateral tracking shot），
    模型对这类词的识别更准。

只输出 JSON，不要任何解释文字：
{"shots": [{"shot_id": 1, "visual_prompt": "...", "negative_prompt": "...", "video_prompt": "...", "audio": "..."}]}"""


def director(
    user_input: str,
    characters: list[dict] | None = None,
    source_text: str | None = None,
) -> Project:
    """阶段一：一句创意（或一篇小说）-> 全局故事设定（含角色锚点）。

    characters 是用户在界面上预先配置的角色 [{"name":..., "anchor":...}]——
    anchor 可以留空，留空的由 AI 设计。source_text 是用户上传的小说/剧本
    原文：给出时整个故事走"改编"模式，而不是从创意自由发挥。
    """
    user_parts = []
    if source_text:
        text = source_text.strip()
        if len(text) > 80000:   # 1M 上下文装得下，但截断兜底防异常大文件
            text = text[:80000] + "\n…（原文过长，此处截断）"
        user_parts.append(
            "以下是一篇用户提供的小说/剧本原文。请把它改编成可拍摄的短片故事："
            "提炼最值得影像化的核心情节，压缩成一部短片能承载的体量，"
            "保留主角和关键配角，不要照搬全文：\n" + text
        )
    user_parts.append(f"用户的创意：\n{user_input}")

    if characters:
        lines = []
        for c in characters:
            name = str(c.get("name", "")).strip()
            if not name:
                continue
            anchor = str(c.get("anchor", "")).strip()
            if anchor:
                lines.append(f"  - {name}：{anchor}（锚点必须原样保留，只允许做细节润色）")
            else:
                lines.append(f"  - {name}：外貌锚点由你设计（写成固定的、可复述的视觉特征）")
        if lines:
            user_parts.append(
                "故事必须使用以下角色，不得增删、不得改名：\n" + "\n".join(lines)
            )

    data = chat_json(DIRECTOR_SYSTEM, "\n\n".join(user_parts), temperature=0.8)
    project = Project.from_dict(data)

    # 用户配置的角色名强制生效（AI 偶尔会改名），用户写死的锚点也强制保留
    if characters:
        pre_map = {
            str(c.get("name", "")).strip(): c
            for c in characters
            if str(c.get("name", "")).strip()
        }
        for c in project.characters:
            pre = pre_map.get(c.name)
            if pre:
                user_anchor = str(pre.get("anchor", "")).strip()
                if user_anchor:
                    c.anchor = user_anchor
    return project


def storyboard(project: Project, shot_count: int | None = None) -> list[Shot]:
    """阶段二：全局设定 -> N 个分镜（此阶段还不含出图提示词）。

    shot_count=None 时由分镜 Agent 按故事节奏自行决定数量（短片的常见用法）。
    """
    if shot_count:
        count_req = f"请拆出 {shot_count} 个分镜。"
    else:
        count_req = "分镜数量由你决定：以把故事讲完整、节奏自然为准，通常在 8 到 30 个之间。"
    user = (
        f"故事设定：\n"
        f"标题：{project.title}\n"
        f"故事：{project.logline}\n"
        f"风格：{project.style}\n"
        f"角色：\n"
        + "\n".join(f"  - {c.name}：{c.anchor}" for c in project.characters)
        + f"\n\n{count_req}"
    )
    data = chat_json(STORYBOARD_SYSTEM, user, temperature=0.7)

    shots: list[Shot] = []
    for idx, raw in enumerate(data.get("shots", []), start=1):
        shots.append(Shot.from_dict(raw, shot_id=idx))
    if not shots:
        raise RuntimeError("分镜 Agent 未返回任何分镜")
    return shots


def prompt_engineer(project: Project, shots: list[Shot]) -> None:
    """阶段三：就地填充每个分镜的 visual_prompt / negative_prompt / video_prompt。

    两种提示词在同一路调用里产出，而不是拆成两个 Agent：
    visual_prompt 给图像模型出首帧图，video_prompt 配首帧图给图生视频模型。
    拆开会让同一份分镜上下文重复发送一遍，没必要。
    """
    lines = []
    for s in shots:
        lines.append(
            f"[{s.shot_id}] 画面：{s.scene_desc} | 景别：{s.camera} | 运镜：{s.motion}"
            f" | 时长：{s.duration:g}s | 台词：{s.dialogue or '无'}"
            f" | 出场角色：{', '.join(s.character_refs) or '无'}"
        )

    anchors = "\n".join(
        f"  - {c.name}：{c.anchor}" + (f"｜音色：{c.voice}" if c.voice else "")
        for c in project.characters
    )
    user = (
        f"全局风格：{project.style}\n"
        f"角色锚点：\n{anchors}\n\n"
        f"分镜列表：\n" + "\n".join(lines)
    )
    data = chat_json(PROMPT_SYSTEM, user, temperature=0.5)

    by_id = {int(item["shot_id"]): item for item in data.get("shots", []) if "shot_id" in item}
    missing_video: list[int] = []
    for s in shots:
        item = by_id.get(s.shot_id)
        if item:
            s.visual_prompt = str(item.get("visual_prompt", ""))
            s.negative_prompt = str(item.get("negative_prompt", ""))
            s.video_prompt = str(item.get("video_prompt", ""))
            # 声音设计只在模型真的返回了才覆盖：单镜改写时若模型没给 audio，
            # 保留用户手填/上一轮的声音设计，而不是清空
            if item.get("audio"):
                s.audio = str(item.get("audio"))
        if not s.visual_prompt:
            raise RuntimeError(f"分镜 {s.shot_id} 未获得 visual_prompt")
        if not s.video_prompt:
            # 视频链路尚未接入，一个暂时不被消费的字段不该让整条管线失败
            missing_video.append(s.shot_id)
    if missing_video:
        print(f"      警告：分镜 {missing_video} 未获得 video_prompt")
