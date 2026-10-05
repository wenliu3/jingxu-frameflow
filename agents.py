"""三个文本 Agent。

职责边界刻意切在「叙事语言」和「视觉语言」之间，而不是按步骤切：

  导演 -> 分镜  ：处理的都是给人看的故事语言
  提示词        ：把故事语言翻译成图像模型能执行的画面语言

这个转换无法省略。「他感到失落」对图像模型是无效输入，
必须变成「低着头，雨水沿下颌滴落，顶光勾出脸的轮廓」。
把它单独成 Agent，将来换图像模型时只需要改这一个文件。
"""

from __future__ import annotations

import os

from schemas import Project, Shot
from llm import chat_json
import ref_plan

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
6. 每个角色和每条素材都要给**英文版锚点**（anchor_en）：模型里 FLUX 系的文本编码器
   只吃英文，中文 prompt 对它是噪音。英文锚点不是逐字翻译，是同信息的英文重述
   （"28岁亚洲男性，黑色短发，左眉旧疤，深蓝冲锋衣" ->
    "28-year-old Asian man, short black hair, thin faint old scar above left eyebrow, dark blue windbreaker"）。
   禁止在英文里引入中文锚点没有的特征。疤痕/痣这类小特征必须带程度词
   （thin faint / small subtle）——实测 FLUX 会把没有程度限定的 scar 画成大块糜烂伤疤。
7. style 也要给英文版 style_en（视觉风格 + 色调 + 质感的英文重述）。
8. 同时拆出道具和场景两类**素材**（与角色同级，都会生成参考图、跨分镜复用）：
   - props 道具 0-4 个：只列**对叙事有作用的**（剧情道具、反复出现、能被特写的），
     路人甲的雨伞不要列。anchor 写材质/颜色/形状/磨损细节（"掌心大小的黄铜罗盘，
     盘面刻星宿纹，指针氧化发黑，边缘有磕碰缺口"）。
   - scenes 场景 1-3 个：故事发生地的**环境设定**（不是任何一镜的画面）。
     anchor 写空间结构 + 光源方向 + 材质质感（"城中村天台，竹竿晾衣绳纵横，
     晨光从右侧楼缝斜切进来，水泥地面有积水反光"）。场景图会作为空镜参考，
     所以 anchor 里**禁止出现任何人物**。
   - 两者都禁止写"帅气""温馨"这类情绪词——它们落不到画面。
9. 命名规范：道具/场景的 name 用不超过 6 个字的中文短语，会在后续分镜里被引用。

只输出 JSON，不要任何解释文字：
{
  "title": "作品标题",
  "logline": "一句话故事",
  "style": "视觉风格 + 色调 + 质感",
  "style_en": "visual style + palette + texture, restated in English",
  "aspect_ratio": "16:9",
  "characters": [
    {"name": "主角A", "anchor": "28岁亚洲男性，黑色短发，左眉有一道旧疤，深蓝色冲锋衣", "anchor_en": "28-year-old Asian man, short black hair, thin faint old scar above left eyebrow, dark blue windbreaker", "voice": "低沉沉稳的青年男声"}
  ],
  "props": [
    {"name": "黄铜罗盘", "anchor": "掌心大小的黄铜罗盘，盘面刻星宿纹，指针氧化发黑，边缘有磕碰缺口", "anchor_en": "palm-sized brass compass, star-map engravings on the dial, oxidized black needle, chipped edges"}
  ],
  "scenes": [
    {"name": "城中村天台", "anchor": "城中村天台，竹竿晾衣绳纵横，晨光从右侧楼缝斜切进来，水泥地面有积水反光", "anchor_en": "rooftop of an urban village, bamboo drying poles crisscrossing, morning light slanting through a gap between buildings, puddles reflecting light on the concrete"}
  ]
}"""


STORYBOARD_SYSTEM = """你是一位分镜师。根据给定的故事设定，拆出指定数量的分镜。

每个分镜必须包含以下字段：
- scene_desc：一句中文画面描述，写清楚「谁、在哪、做什么、什么光线」
- camera：景别，只能从 远景 / 全景 / 中景 / 近景 / 特写 中选一个
- motion：运镜，从下面这份 H3 官方运镜词表里选**一个**，直接填英文术语：
  固定机位 Static Shot / 推近 Push In / 拉远 Pull Out / 变焦推 Zoom In / 变焦拉 Zoom Out /
  水平摇 Pan Left / Pan Right / 垂直摇 Tilt Up / Tilt Down /
  水平平移 Truck Left / Truck Right / 机位升降 Pedestal Up / Pedestal Down /
  环绕 Arc Shot / 跟拍 Tracking Shot / 主观视角 POV /
  机身旋转 Roll Clockwise / Roll Counterclockwise / 轻微晃动 Shake Slightly
  注意区分：Zoom 是机身不动、焦距变；Push / Pull 是机身前后移动。
  用英文原词比中文译名识别更准，这也是官方词表的形式。
- duration：该镜时长（秒），**由你按节奏判断，2 到 10 之间**，小数随意。
  判断依据（这是 H3 的实测特性，不是风格偏好，请按它权衡）：
  · H3 的官方训练区间是 4-15 秒，**4 秒以下落在区间外，画面稳定性会明显下降**。
    所以 2-3 秒只留给「本来就要短促」的地方：快速动作切口、反应镜头、闪回。
    不要为了赶节奏把常规叙事镜头压到 3 秒以下。
  · 需要走完一个完整动作过程的（走位、转身、递接物品、开门进屋）给 6-10 秒。
    时间不够，动作会被压缩成跳变，反而更差。
  · 定场、抒情、需要呼吸感的 5-8 秒。
  · 常规叙事与对白镜头 4-6 秒；台词长的按语速估（中文约每秒 4-5 字）。
  · 上限不超过 10 秒：H3 上限是 15 秒，但生成耗时与帧数成正比，长镜头代价高。
  · 代码把时长就近吸附到 17 帧网格（24fps，格距约 0.71 秒）。**建议直接按网格档位给值**，
    省掉吸附误差。2-10 秒区间内的合法档位：
    2.33 / 3.04 / 3.75 / 4.46 / 5.17 / 5.88 / 6.58 / 7.29 / 8.00 / 8.71 / 9.42 / 10.12 秒
  · ⚠️ 吸附陷阱：请求 4.0 秒会被就近吸到 3.75 秒，**反而掉回 4 秒下限之外**。
    想稳在 4 秒以上，就从 4.46 秒起给。
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
{"shots": [{"scene_desc": "...", "camera": "中景", "motion": "缓慢推镜", "duration": 2.5, "transition": "cut", "dialogue": "", "character_refs": ["主角A"], "prop_refs": ["黄铜罗盘"], "scene_refs": ["城中村天台"]}]}

prop_refs / scene_refs 说明：
- 从故事设定给出的素材名单里选（名单会附在下方），**不要自创名字**
- prop_refs：该镜画面里真实可见的道具，没有就空数组
- scene_refs：该镜所在的环境，**至多一个**；没有明确环境（如纯黑背景特写）就空数组
- 这两个字段会把对应素材的参考图带进该镜的出图参考，标错会污染画面"""


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

【二、video_prompt + audio：配首帧图送 MiniMax H3，驱动画面动起来并生成原生音频】

H3 的提示词不是关键词堆，也不是散文，而是一份**结构化控制文档**。官方规定三个
固定字段，顺序不能乱（这类模型对开头内容的权重最高）：
    integrated_multimodal_description → overall_soundscape → non_diegetic_music
**你只写前两个字段的正文**：video_prompt 写第一个，audio 写第二个。
字段名本身、以及第三个字段（配乐，默认 N/A）由代码拼装，你不要写进正文。

【硬性要求：正文全部用英文写】
官方提示词指南（h3-prompt-writing / base-en.txt）明确要求内容用英文，
**只有台词保留原语言**（中文台词就照抄中文）。不要中英混写——混写会稀释控制信息。

7. video_prompt = integrated_multimodal_description 的正文。
   每个分镜就是一个**连续镜头**（不切镜、不切时间段），按「连续链条」写：
   起始构图 → 主动作 → 相机反应 → 状态变化 → 结束构图。
   a) 以 [Shot 1] 开头，紧跟风格词（Live-action, cinematic / 2D-animated /
      3D CG / claymation…），再交代起始构图。
   b) **声明保持，但不复述外貌**。写 "the woman shown in [Picture 1] remains…,
      preserving her appearance, clothing and the room layout" 这类**保持声明**。
      绝对不要把发型、服装、疤痕、信物逐项列出来——首帧图已经携带这些信息，
      复述会让模型推翻首帧重新生成，表现为闪烁和形变，这是图生视频最常见的失败模式。
   c) 运镜写成自然英语句子，格式是「类型 + 幅度 + 速度」，幅度与速度只在有意义时写：
      The camera pushes in with small amplitude at slow speed toward her hands.
      只用 motion 字段给的那一个运镜，不要叠加两个。
   d) 动作用英文的自然衔接（as / while / then / before）连贯展开，
      不要用「先…接着…」这类中文连接词。
   e) 光影写方向和质量的物理描述（"hard top light from upper right"、
      "soft light diffused through a gauze curtain"），不要写 "cinematic mood"
      这类情绪词。也不要写景别和时长——它们分别由首帧图和生成参数决定，写进去只会干扰。
   f) 有台词时按官方语法写：
      · 说话人用稳定 ID (S1)、(S2)，同一角色在所有分镜用同一个 ID
      · 音色描述放在 (S1) **外面**：the quiet, breathy young woman (S1) says:
      · ⚠️ 同一角色的音色描述短语在所有分镜**逐字相同**（第一次出现时定稿，
        后面原样复制）——H3 按描述即兴生成嗓音，措辞一变音色就漂，
        "quiet, breathy" 和 "soft-spoken" 出来的是两个人
      · 台词原文包在方括号语言标签里，逐字保留、不翻译、不改标点：
        [English] I get off at the next station. ／ [Chinese] 我在下一站下车。
      · 画外音用 says in an off-screen voiceover，并补 while her lips remain
        completely closed，否则模型会让嘴动
      · 画面里真实可见的招牌/标牌文字用双引号包裹，逐字保留
   g) 篇幅 60-120 个英文词。官方示例的中位长度约合 130 个字符的信息量，
      超过这个量不再增加控制，只会引入矛盾。

8. audio = overall_soundscape 的正文，也就是这一镜的**环境音**（1-4 句）。
   写这个空间里真实存在的声音：环境底噪、动作音、非人声。
   例：Rain taps steadily against the window, a kettle begins to hiss, and distant
   traffic hums beyond the glass.
   · 只写画内听得到的声音（角色自己听得到的）
   · **不要在这里写对白**——台词属于 video_prompt
   · **不要在这里写配乐**——配乐是第三个字段，代码默认写 N/A（只要环境音），
     所以这里一个字都不要提 music / 音乐 / 配乐 / BGM
   · 也用英文写。H3 的音视频是同一轮生成的，声音没交代清楚它会自己编一套。

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
    # 导演按 props / scenes 两个顶层数组返回；统一折叠进 Project.assets（kind 区分）
    flat_assets: list[dict] = []
    for kind in ("props", "scenes"):
        for item in data.pop(kind, []) or []:
            if isinstance(item, dict) and str(item.get("name", "")).strip():
                flat_assets.append({"kind": kind.rstrip("s"), **item})
    data["assets"] = flat_assets
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
    asset_lines = [
        f"  - [{a.kind}] {a.name}：{a.anchor}" for a in project.assets if a.name
    ]
    user = (
        f"故事设定：\n"
        f"标题：{project.title}\n"
        f"故事：{project.logline}\n"
        f"风格：{project.style}\n"
        f"角色：\n"
        + "\n".join(f"  - {c.name}：{c.anchor}" for c in project.characters)
        + (
            "\n素材名单（prop_refs / scene_refs 只能从这里选，不要自创）：\n"
            + "\n".join(asset_lines)
            if asset_lines
            else ""
        )
        + f"\n\n{count_req}"
    )
    data = chat_json(STORYBOARD_SYSTEM, user, temperature=0.7)

    shots: list[Shot] = []
    for idx, raw in enumerate(data.get("shots", []), start=1):
        shots.append(Shot.from_dict(raw, shot_id=idx))
    if not shots:
        raise RuntimeError("分镜 Agent 未返回任何分镜")
    return shots


# ---------------------------------------------------------------- 分块流程
# 块 = 一条 10s 视频。分块 Agent 不再一次性拆完全片，而是每次只产出「下一块」：
# 用户逐块验收，不满意可以在指令里纠偏，满意了再要下一块。

NEXT_BLOCK_SYSTEM = """你是一位短片导演，正在用「分块」的方式推进一部短片。
每一块 = 一条 10 秒的连续镜头视频。你会拿到：故事设定、可用资产名单（角色/道具/场景）、
已经完成的块（按顺序），以及用户对这一块的指令。你的任务是产出**下一个块**。

只输出 JSON，不要任何解释文字。

■ 如果故事还没讲完，输出下一块：
{
  "done": false,
  "block": {
    "summary": "一句中文：这一块讲什么（写给用户看的剧情梗概）",
    "characters": ["角色名"],          // 只能从角色名单里选，按戏份排；没有人物就空数组
    "props": ["道具名"],               // 只能从道具名单里选，只列画面里真实可见的
    "scene": "场景名",                 // 至多一个，只能从场景名单里选；无明确环境填空串
    "dialogue": "旁白：……\\n角色A：……", // 这块的台词/旁白，没有就空串。说话人用「名字：」行式
    "beats": [                          // 10 秒切成 3-5 个节拍，时间必须连续且铺满 0-10s
      {"start": 0, "end": 3, "action": "中文：这几秒谁在做什么、画面什么样", "camera": "这几秒的景别/运镜，可空"},
      {"start": 3, "end": 6, "action": "...", "camera": "..."},
      {"start": 6, "end": 10, "action": "...", "camera": "..."}
    ],
    "camera": "主景别",                 // 远景/全景/中景/近景/特写 选一个
    "motion": "主运镜",                 // Static Shot / Push In / Pull Out / Pan Left / Pan Right /
                                        // Tilt Up / Tilt Down / Tracking Shot / Arc Shot / POV 等英文术语选一个
    "avoid": "中文：不该出现什么",       // 具体的画面禁忌（如“不要切换场景，不要出现其他路人”），没有就空串
    "continue_last": false              // true = 本块从上一块的最后一帧接着演（同一场景的连续动作，
                                        //   或上一块的戏 10 秒没演完）；场景/时间切换必须 false。第一块必须 false
  }
}

■ 如果故事已经讲完（所有块合起来能收尾了），输出：
{"done": true, "reason": "一句话：故事在哪里收住了"}

分块创作规则：
1. **一次只出一个块**。它是已有块的直接后续：剧情要衔接，情绪要递进。不要提前规划后面所有块，
   也不要在 summary 里剧透还没拍的走向。
2. 每块 10 秒只够「一小段戏」：一个动作过程、一次交锋、一个情绪转折。别贪多，
   装不下的内容留给下一块——这正是块可以承接尾帧的意义。
3. beats 的 action 必须是能拍出来的画面（谁、在哪、做什么），禁止心理活动；
   时间轴必须铺满 0 到 10 秒且互不重叠，节拍 3-5 个。
4. 角色出场要克制：单块角色不超过 2 人，多了画面会乱。
5. 剧情道具才进 props；场景没有明确切换就沿用上一块的 scene。
6. 用户指令是最高优先级：他点名要谁出场、要什么剧情，就照办；
   指令与剧情连贯性冲突时，服从指令并在 summary 里自然圆过去。
7. avoid 写**画面层面的禁止**（镜头语言、构图、不该出现的东西），
   不要写“不要无聊”这种没法执行的词。"""


BLOCK_PROMPT_SYSTEM = """你是提示词工程师，为一个「块」写提示词。一块 = 一条 10 秒的连续镜头视频，
用法与单镜提示词一致，但正文必须按秒交代节拍。

【一、visual_prompt：给图像模型，产出这一块的首帧图（第 0 秒的画面）】
硬性要求：
1. 输出英文，逗号分隔的短语堆叠，不要完整句子。
2. 必须原样包含本块所有出场角色的 anchor 英文版，放在提示词前部。
3. 拼入全局 style 的英文版，放在角色锚点之后。
4. 场景非空时，把场景锚点翻译成英文环境描述拼进去（空间结构 + 光源方向）。
5. 根据 camera 补视角词：特写->close-up shot，近景->medium close-up，
   中景->medium shot，全景->full shot，远景->wide shot。
6. negative_prompt 对人物类画面统一使用：
   extra fingers, extra limbs, fused fingers, distorted face, deformed hands,
   malformed limbs, text, watermark, signature, lowres, blurry, jpeg artifacts

【二、video_prompt + audio：配首帧图送 MiniMax H3，10 秒连续镜头】

H3 的提示词是一份结构化控制文档。video_prompt 写 integrated_multimodal_description
的正文，audio 写 overall_soundscape 的正文。全部用英文写，只有台词保留原语言。

7. video_prompt 的结构（顺序固定）：
   a) 以 [Shot 1] 开头 + 风格词（Live-action, cinematic / 2D-animated / 3D CG…），
      再按 beats 的第一个节拍交代起始构图。
   b) **声明保持，但不复述外貌**：写 "the woman shown in [Picture 1] remains…,
      preserving her appearance, clothing and the room layout" 这类保持声明。
      绝对不要把发型服装逐项复述——首帧图已携带这些信息，复述会导致闪烁形变。
   c) **按秒铺节拍**：把 beats 翻译成带时间锚的英文连续句，格式如
      "From 0 to 3 seconds, ...; from 3 to 6 seconds, ...; in the final 4 seconds, ..."
      动作用 as / while / then 自然衔接，每个节拍里点到对应角色的动作与镜头变化。
   d) 运镜写「类型 + 幅度 + 速度」的自然英语（The camera pushes in with small
      amplitude at slow speed toward her hands.）。只用 motion 字段给的那一个主运镜。
   e) 台词按官方语法：说话人用稳定 ID (S1)/(S2)（映射见下方角色名单，**全片不变**）；
      音色描述放在 (S1) 外面且**逐字复用名单里给的英文音色短语**，不要自己重新翻译；
      台词原文包在方括号语言标签里逐字保留：[Chinese] 我在下一站下车。
      画外音补 says in an off-screen voiceover 与 while her lips remain completely closed。
   f) avoid 非空时，在正文末尾加一句 "Avoid: ..." 短句（英文），把禁忌逐条写成
      具体的画面禁止（Avoid: no scene cuts, no other people entering the frame.）。
      不确定的不写，avoid 为空就完全不要出现 Avoid 字样。
   g) 篇幅 80-150 个英文词。10 秒的块比单镜长，允许写满，但不要注水。

8. audio = overall_soundscape 的正文（1-4 句英文环境音）：画内真实存在的
   环境底噪、动作音、非人声。不写对白，不写配乐（music 一类字眼禁止出现）。

只输出 JSON，不要任何解释文字：
{"visual_prompt": "...", "negative_prompt": "...", "video_prompt": "...", "audio": "..."}"""


def design_character(project: Project, name: str, hint: str = "") -> dict:
    """为手动添加的角色设计锚点与音色（用户没写 anchor 时的兜底）。

    与导演 Agent 同一套锚点纪律：固定、可复述的视觉特征，禁止情绪词；
    anchor_en 与 voice 一并产出，让 FLUX 定妆照与 H3 配音两条链路都能吃。
    """
    system = """你是选角导演。根据故事设定为一个新角色设计形象。
锚点必须是固定的、可复述的视觉特征（年龄、发型、发色、服装、显著特征），
禁止"帅气""忧郁"这类落不到画面的词。疤痕/痣等小特征必须带程度词（thin faint / small subtle）。
anchor_en 是同信息的英文重述，禁止引入中文锚点没有的特征。
voice 是声音设计：年龄感 + 音色 + 语速语气（例如"清亮的少女音，语速偏快"）。
只输出 JSON：{"anchor": "...", "anchor_en": "...", "voice": "..."}"""
    known = "\n".join(f"  - {c.name}：{c.anchor}" for c in project.characters) or "  （暂无其他角色）"
    user = (
        f"故事：{project.logline}\n风格：{project.style}\n"
        f"已有角色：\n{known}\n\n新角色：{name}"
        + (f"\n用户补充：{hint.strip()}" if hint.strip() else "")
    )
    data = chat_json(system, user, temperature=0.7)
    return {
        "anchor": str(data.get("anchor", "")).strip(),
        "anchor_en": str(data.get("anchor_en", "")).strip(),
        "voice": str(data.get("voice", "")).strip(),
    }


# 四视图设定图的**版式段**：由代码拼死，不让文本模型自由发挥。
# 理由与「对齐指令必须由代码写」同源 —— 这不是妙笔生花的地方，是给 crop_ref 切割用的
# 结构约束：① 视角顺序固定，切出来的语义名才是对的；
# ② 明写"格与格之间留空白间隔"，crop_ref 的白缝检测要靠它；
# ③ 明写"不出现任何文字"，模型很爱在设定图边上加标注文字。
#
# ⚠️ 2026-09-19 晚：**定稿为 16:9 横版四格**。同一天早些时候一度改成「竖版 9:16 左右两栏」
# （依据是豆包样张，实验产物 `outputs/_collage_test.png` 等），但斌哥随后给的参考图就是
# **横版四格** —— 图本身是「左侧超大面积面部半身特写 + 右侧依次正面 / 标准侧面 / 背面全身」，
# 图下面附的那段提示词也明写「四视图横向排版……16:9 横版构图」。前端弹窗里一直写着的
# 也是这句（`api.js` / `CreateWorkbench` 的 title）：**四视图设定图固定 16:9**。
#
# 出图实证（两张都在仓里，可以直接对比）：
#   `金鳞_sheet.png`（1024×576 横版四格）四个视角、同一人物、同一套服装，全对；
#   `莫卡_sheet.png`（576×1024 竖版两栏）出成了「两张一模一样的正面全身」——
#   右栏三格一格都没排出来、左栏特写整格丢掉。竖版反而压不住，横版更稳。
#
# ⚠️ 硬约束必须保留（旧版只有描述、没有禁令，实测会跑偏）：
#   实锤 `莫卡_sheet.png` 横版那一版出成过「正面全身 / 正面全身（与第 1 格重复）/
#   45° 四分之三侧 / 背面」—— 面部特写整格丢掉、标准 90° 侧面变成 45°。
#   所以把四件事写成硬约束：顺序不许变、恰好四格、互不相同、左格不许画成全身。
PORTRAIT_SHEET_LAYOUT = """16:9 横版角色设定图，四个视角从左到右横向排成一行，间隔均匀、互不重叠。
最左侧是一张巨大的面部半身特写，占画面宽度约三分之一、上下贯穿整个画面，它是全图的重点：
人物正脸端正直视镜头，只画头部、肩颈与胸口以上（绝对不要画成全身），
脸要尽量大、五官最清晰，完整展示妆容、发型、耳饰、项链与肩颈。
它的右边依次是正面全身、标准侧面全身（90 度正侧向）、背面全身，三格各占剩余宽度的三分之一，
头顶到脚完整入画，且三格必须与左侧特写是同一位人物 ——
正面全身双手自然下垂、标准站姿，完整展示正面服饰结构；
侧面全身肩线侧对镜头，展示鼻梁、下颌线、胸腰比例与服装侧面轮廓；
背面全身背对镜头，完整展示后发层次、背部服装结构与鞋袜背面。
四个视角的人物身高、体型、发长、五官、服装、配饰完全一致，是同一人物的不同角度，
不允许换装、不允许改发色、不允许改变体型，身材比例自然，拒绝幼态化与头身比例异常。
恰好这四个视角（一个面部特写 + 三个全身），不允许两格重复同一个视角，也不允许缺任何一个视角。"""

PORTRAIT_SHEET_TAIL = """背景为浅灰暖色影棚背景，四个视角的背景完全一致，柔和均匀光线、无生硬阴影。
超高清，细节锐利，画面中不出现任何文字、水印、logo、边框、编号或标注。
只按上面说的横向四格排版：不要竖排两栏，不要三张全身像并排的转身图，不要九宫格，不要更多格子。
16:9 横版构图。"""


def compose_portrait_sheet(project: Project, name: str, hint: str = "") -> dict[str, str]:
    """把用户一句话的角色描述，扩写成一组「角色设定图」提示词。

    返回 `{"body": 人物本体描述段, "sheet": 完整四视图设定图提示词}`。
    两条产线共用同一个 `body` —— 四视图总览与正面单张图靠**同一段文字**保证是同一个人。

    为什么必须扩写：模型要极具体的五官/发型/服装/配饰描述才画得住；用户写的
    "酷一点的赛博女战士"直接进模型，四个视角会各画各的、细节全飘。

    版式段与收尾段由代码拼（PORTRAIT_SHEET_LAYOUT / PORTRAIT_SHEET_TAIL）——
    文本模型只负责"这个人长什么样"，不负责"怎么排版"。

    ⚠️ 2026-09-19 晚：设定图提示词的**顺序改成「版式段 + 人物段 + 收尾段」**。
    原来人物段在最前面，实测两份都吃亏（莫卡那张设定图连着三次都丢掉左格的面部特写、
    退化成"三张全身像"的转身图）：① 人物段 641 字写在最前，把"左格必须是特写"这条指令
    压到 600 字之后，注意力衰减到几乎不起作用；② 人物段里还混着"角色四视图设定图"
    "站立时重心压在右腿"这类**版式词 + 全身姿势**，等于在正面鼓励模型画全身拼图。
    现在版式段打头（构图是最不能丢的那条），人物段只写"这个人长什么样"（已禁止版式词与动作描写）。
    """
    system = """你是角色设定图的美术。把用户对某个角色的描述，扩写成**人物本体描述段**（这个人长什么样）。
只写这个人本身 —— 版式、背景、画质、画幅另有固定模板，你一个字都不要写。

按下面顺序写成**一段连贯的中文**（不要分点、不要小标题、不要换行）：
1. 风格标签：开头给一组风格标签（如"高精度 3D 半写实国漫风，半写实三维渲染结合手绘质感""2D 厚涂赛璐璐动画风""写实电影质感"）。
2. 气质与体态：年龄段、身高体型、气质（**只写"是什么样的人"，不要写动作、手势、重心、手里拿什么、正在做什么** —— 设定图里人物自然站立，姿势由版式模板负责）。
3. 面部：脸型、下巴、下颌线、眉、眼睛（瞳色/眼型/睫毛）、鼻、唇、肤色、妆容（腮红、亮片、唇色）。
4. 发型：发色、长度、卷直、刘海、发缝、头顶蓬松度、发尾形态。
5. 服装：上衣与下装的款式、版型、材质、颜色、图案、剪裁细节，以及整体配色关系。
6. 配饰与鞋袜：耳饰、项链、戒指、鞋、袜的材质与颜色。

硬性要求：
- 全中文，**250-400 字**（写太长模型抓不住重点），只写画面能看见的东西。
- **一个字都不要提版式**：「四视图」「设定图」「构图」「画幅」「排版」「横向」「竖版」「几格」「全身立绘」这类词一律不许出现。
  这段文字会同时拼进**单张定妆照**的提示词里 —— 里面只要出现"四视图""设定图"，单张就会被模型画成多格拼图。
- 每项都要具体到能画出来（如"偏大的灰蓝灰棕玻璃感瞳孔""高腰拼色短裤，焦糖棕为主体配低饱和鼠尾草绿拼布""长度过腰的自然大波浪，中分八字空气刘海"）。禁止"漂亮""帅气""有气质""很酷"这类落不到画面的词。
- 不要写镜头参数、不要写"8K/高清"、不要写否定句、不要写剧情或情绪渲染。
- 角色不是人类时（动物/机甲/拟人），照样按"外观特征"逐项写清楚，不要硬套人类五官。
- 只输出 JSON：{"prompt": "<人物本体描述段>"}"""
    known = "\n".join(
        f"  - {c.name}：{c.anchor}" for c in project.characters if c.anchor
    ) or "  （暂无其他角色）"
    user = (
        f"故事：{project.logline}\n全局风格：{project.style}\n"
        f"已有角色（避免撞脸）：\n{known}\n\n"
        f"要设计的角色：{name}"
        + (f"\n用户对这个角色的描述：{hint.strip()}" if hint.strip() else "")
    )
    data = chat_json(system, user, temperature=0.8)
    body = str(data.get("prompt", "")).strip()
    if not body:
        raise RuntimeError("角色设定图描述生成失败：模型没有返回内容")
    return {
        "body": body,
        # ⚠️ 版式段必须在最前（见 docstring 里 2026-09-19 那条）：
        #    构图相关的指令一旦被 600 字的人物描述压到后面，模型基本就当没看见。
        "sheet": f"{PORTRAIT_SHEET_LAYOUT}\n人物特征：{body}\n{PORTRAIT_SHEET_TAIL}",
    }


def design_asset(project: Project, kind: str, name: str, hint: str = "") -> str:
    """为手动添加的素材设计提示词描述。纪律与导演 Agent 的素材要求一致。

    kind：
      - scene：空镜环境（环境 + 镜头 + 光影，画面里没有人）
      - prop ：单件道具的外观（供三视图设定图用）
      - image：**一整张完整画面**（2026-09-17 加）。「其他图片」在流程里是首尾帧与
        Ref2VA 参考图的来源，要的是能直接用的画面，不是设定图 —— 所以这一支
        允许有人物、有动作、有环境，只要构图与光影成立。
    """
    if kind == "scene":
        system = """你是场景美术。为故事设计一个场景的**空镜环境**描述，供图像模型出场景设定图。

按三段写，缺一不可：
1. 环境：这是什么地方、空间结构与尺度、主要陈设与材质质感（具体到能画：砖墙、铁皮顶、竹竿、积水反光…）。
2. 镜头：景别与视角（如"广角全景，平视，正对入口"）、构图关系（前景/中景/远景各有什么）。
3. 光影：光源种类与方向、色温、明暗对比（如"侧逆光，暖黄路灯从右侧斜切，地面湿润反光"）。

硬性要求：
- 画面里**绝对不出现任何人物**：不要人、不要背影、不要剪影、也不要有人的影子。
- 不写人物动作、不写剧情、不写情绪词（"温馨""大气""帅气"这类一律禁止）。
- 200-400 字，只写画面能看见的东西；不要写镜头参数（焦距/光圈），不要写"8K/高清"。
- 只输出 JSON：{"anchor": "..."}"""
    elif kind == "image":
        system = """你是分镜美术。为故事画一张**独立完整的画面**，供图像模型出图参考。

按三段写，缺一不可：
1. 主体与动作：画面里有什么、在做什么、彼此的位置关系与朝向（给可感知的尺度参照，如"半身入画""约 1.7 米高"）。没有人物时写清主体是什么、处于什么状态。
2. 环境与陈设：所处空间、主要物件与材质质感（具体到能画：水泥墙、铁艺栏杆、地面积水…）。
3. 镜头与光影：景别与视角（如"中景，平视，略低机位"）、构图关系（前景/中景/远景各有什么）、光源种类与方向、色温、明暗对比。

硬性要求：
- 这是**一整张能直接用的画面**，不是设定图、不是多视角拼图、不是纯白背景抠图 ——
  画面必须完整（有环境、有光影、有前后层次），构图要能独立成立。
- 只写这**一个瞬间**的画面，不写"接下来""然后"这类时间推进，不写剧情旁白。
- 不写镜头参数（焦距/光圈/胶片型号），不要写"8K/高清/超清"。
- 情绪词至多留一个最贴切的（"压抑""温暖"），禁止堆砌形容词。
- 150-350 字，只写画面能看见的东西。
- 只输出 JSON：{"anchor": "..."}"""
    else:
        system = """你是道具美术。为故事设计一件道具的**外观设定描述**，供图像模型出三视图设定图。

按下面写全（缺一项，三个视角就会画得不一致）：
1. 品类与用途：这是什么、大致尺寸（给可感知的参照，如"掌心大小""约 30 厘米长"）。
2. 形状与结构：整体轮廓、主要部件、比例关系、开合或连接结构。
3. 材质与表面处理：金属 / 木 / 塑料 / 布革等，哑光还是反光，涂层、纹理、划痕。
4. 颜色：主色 + 点缀色，新旧程度与磨损痕迹（缺口、氧化、包浆、褪色）。

硬性要求：
- 只写这**一件**道具，画面里不出现人物、不出现其他物品、不出现摆放环境。
- 不写情绪词（"神秘""帅气"这类禁止）、不写剧情、不写镜头参数、不要写"8K/高清"。
- 150-300 字，只写画面能看见的东西。
- 只输出 JSON：{"anchor": "..."}"""
    known = "\n".join(f"  - [{a.kind}] {a.name}：{a.anchor}" for a in project.assets) or "  （暂无其他素材）"
    user = (
        f"故事：{project.logline}\n风格：{project.style}\n"
        f"已有素材：\n{known}\n\n新素材（{kind}）：{name}"
        + (f"\n用户补充：{hint.strip()}" if hint.strip() else "")
    )
    data = chat_json(system, user, temperature=0.7)
    return str(data.get("anchor", "")).strip()


# 道具三视图设定图的**版式段**：同样由代码拼死。视角顺序（正视 → 侧视 → 后视）
# 是硬约束 —— 顺序错了，三个格子画的东西就对不上；"大小一致、水平对齐、间隔均匀"
# 也必须是固定模板，否则模型会把三个视角画成三种尺寸。
PROP_SHEET_LAYOUT = """三视图横向排版：同一件道具，在同一画布内从左至右等距并排展示 —— 正视图、侧视图（标准 90 度侧面）、后视图。
三个视角必须是同一件道具：大小一致、水平对齐、基线齐平，形态、结构、材质、颜色、比例与磨损细节完全一致，只是观察角度不同（同一件东西转了个身，不是三件不同的东西）。
各视角之间留出均匀的空白间隔，不要重叠、不要相互遮挡、不要加底座或支架。"""

PROP_SHEET_TAIL = """纯白色无缝背景，光影柔和均匀、无生硬阴影，无多余元素、无文字、无水印、无标注、无尺寸线、无边框。
16:9 横版构图，道具居中且完整入画，超高清，细节锐利。"""


def compose_prop_sheet(project: Project, name: str, hint: str = "") -> dict[str, str]:
    """道具 Agent：把口语描述扩写成「三视图设定图」提示词（正视 / 侧视 / 后视）。

    返回 `{"body": 外观描述段, "sheet": 完整三视图提示词}`，与角色那套同构。

    与角色的区别只在视角数（道具三个就够：道具不像人物有表情和动态，
    正面/侧面/背面足以锁住结构与材质）与背景（道具用纯白，角色用浅灰影棚）。
    """
    body = design_asset(project, "prop", name, hint)
    return {
        "body": body,
        "sheet": f"{body}\n{PROP_SHEET_LAYOUT}\n{PROP_SHEET_TAIL}",
    }


VOICE_PICK_SYSTEM = """你是配音指导。用户会给你一个角色和一张**可选音色清单**，
你要从中挑出**最贴合这个角色**的那一个音色。

硬性要求：
1. `voice_id` **只能从清单里挑**，必须是清单中出现过的 id，不许自己编、不许改写、
   不许加前后缀（清单里的 id 可能是 `female-yujie` 这种英文，也可能是
   `zh-CN-XiaoxiaoNeural` 这种，原样抄）。
2. **性别是硬约束**：清单里每个音色都标了性别。清单已经按用户指定的性别筛过一轮，
   但仍要自己核对一遍，**绝不允许挑性别不符的**。
3. 用户对声音的描述优先于你对角色的判断：用户说"沙哑"，就别挑清亮的。
4. 判断依据按优先级：用户描述 > 角色的声音设定 > 角色的身份与性格 > 故事题材。
5. `reason` 用一句中文说清为什么挑它（40 字以内）—— 这句话是给用户看的，
   要具体（"偏冷淡克制，贴合他话少的性子"），不要空泛（"很合适"）。

只输出 JSON：{"voice_id": "...", "reason": "..."}"""


def design_voice(project: Project, character, voices, gender: str = "", hint: str = "") -> dict[str, str]:
    """按用户的一段描述，从音色池里挑一个最贴切的音色。

    返回 `{"voice_id": ..., "reason": ...}`。

    `voices` 由调用方给（`tts.list_voices()`）—— **这里不 import tts**，
    否则 agents 会反向依赖音频模块。池子按 gender 先筛一遍再喂给模型：
    缩小候选面既省 token，也降低它跨性别乱挑的概率。

    ⚠️ 这里**不做校验**：模型仍可能编一个池子外的 id（它偶尔会把中文名当 id 回）。
    校验与回退放在 server 那侧（那里能拿到 tts 的匹配逻辑），别在这里判。
    """
    if not voices:
        return {"voice_id": "", "reason": ""}

    pool = [v for v in voices if not gender or v.get("gender") == gender] or list(voices)
    listing = "\n".join(
        f'  - {v.get("id", "")}｜{v.get("cn", "")}｜{v.get("gender", "")}｜{v.get("hint", "")}'
        for v in pool
    )
    gender_cn = {"female": "女声", "male": "男声"}.get(gender, "未指定（按角色自行判断）")

    def _field(obj, key: str) -> str:
        value = obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)
        return str(value or "").strip()

    name = _field(character, "name")
    user = (
        f"故事：{project.logline}\n全局风格：{project.style}\n\n"
        f"角色：{name}\n"
        f"角色的声音设定：{_field(character, 'voice') or '（未设定）'}\n"
        f"角色的形象设定：{_field(character, 'anchor') or '（未设定）'}\n"
        f"用户对这个声音的描述：{hint.strip() or '（没写，按角色自行判断）'}\n"
        f"指定性别：{gender_cn}\n\n"
        f"可选音色清单（只能从中挑，共 {len(pool)} 个）：\n{listing}"
    )
    # 温度压到 0.4：这是一道"从有限选项里选一个"的题，发散没有收益
    data = chat_json(VOICE_PICK_SYSTEM, user, temperature=0.4)
    return {
        "voice_id": str(data.get("voice_id", "")).strip(),
        "reason": str(data.get("reason", "")).strip(),
    }


SEGMENT_SYSTEM = """你是 H3 视频提示词编排师，将当前视频的描述转换为英文正文和环境音。
素材名称、编号与外观锚点已经给定；没有看过图片或上一段视频，不要声称分析过它们。

用户意图优先：保留主体、动作、机位、单镜头/切镜要求、可见文字与对白原文。
前情说明只作为背景，不能把上一镜的动作重新拍进当前视频，也不能替代当前描述。
未明确要求切镜时保持一个连续镜头；按提供的镜头时间轴写，不额外增加镜头。
参考素材不要求每镜全部出现；允许用户明确提出换衣、环境变化与幻想效果。
未请求的角色、对白、故事事件不要添加。不得把参考人物误写成另一性别、年龄或形象。

输出 video_prompt、soundscape 与 retention 三个 JSON 字段。不要输出六段式外壳或 Markdown。
- Ref2VA：风格用一到两句英文放在 [Shot 1] 前，再逐镜使用给定的 <Subject N>/<Picture N>。
- 基础模式：风格放在 [Shot 1] 后，不使用 <Subject N>。
[Shot 1] 不写时间戳；后续使用 [Shot N] At MM:SS.mmm, 且照给定时间轴写。
每镜写清构图、人物位置、环境与光照、动作变化、运镜、当前声音和参考内容生效点。
运镜用自然动作句，必要时写幅度与速度；避免空泛形容词和重复内容。
对白使用稳定 (S1)/(S2) 和 <d>[Language] 原文</d>，可见文字原样保留。
声音写环境底声和物理动作音；不复述对白，不擅自添加配乐。
素材编号只能来自清单，不允许出现未提供的 <Video N> 或 <Audio N>。
需要保留用户要求的事实，篇幅建议不能成为灌水或编造新剧情的理由。
Ref2VA 的 retention 是「素材标签: 保留标记」对象：保留定义的全部特征用 fully_preserved；
换衣、修改背景等只保留部分特征用 partially_preserved；把特征移到其他主体用 attribute_transfer；
只借风格/氛围用 weak_reference。角色图用对应 <Subject N>，纯构图参考用 <Picture N>。
只填实际清单里的标签；基础模式填空对象。不能对明确改变的外观仍标 fully_preserved。

只输出 JSON：{"video_prompt": "...", "soundscape": "...", "retention": {}}"""


# 基础模式（T2VA/I2VA/FL2VA/L2VA）补在 system 后面的一段说明（2026-09-19）。
# 为什么要分模式：两套格式是并列的，<Subject N> 那套标签只属于全参考模式；
# 在基础模式里写编号 = 引用了一堆没声明的标签（模型只能自己猜）。
_BASE_MODE_NOTE = """⚠️ 这一次按**基础模式**写（不是全参考模式）：
- **不要用 `<Subject N>` 编号** —— 那是 Ref2VA 的标签体系，基础模式里没有这套标签。
  素材用自然语言点名（角色名 / 场景名 / 道具名），首帧那张图可以用 `<Picture 1>` 指代。
- **不要写 subject_definitions / summary / retention_analysis 这类字段名** ——
  这一段只产出 `integrated_multimodal_description` 的正文；三段式的外壳与
  「关键帧对齐指令」由代码在出片时拼好（`video_provider.compose_h3_prompt`）。
"""


# 官方规则精要（docs/h3-guides/h3-prompt-rules.md）：**运行时读文件** —— 规则集中在那一份里，
# 官方更新了只改文件、不用改代码，人和 AI 也查同一份。
# ⚠️ 那份是**我们自己写的要点转述**，不是官方原文：官方仓库用的是 MiniMax H3 Community
# License（厂商自定义协议），把原文整份拷进项目不合适。文件头写了来源与逐条核对清单。
_PROMPT_RULES_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "docs", "h3-guides", "h3-prompt-rules.md"
)


def _prompt_rules(ref_mode: bool | None = None) -> str:
    """读规则精要。读不到就返回空串 —— 退化成下面内置的那套规则，绝不让出片失败。"""
    try:
        with open(_PROMPT_RULES_PATH, encoding="utf-8") as fh:
            rules = fh.read().strip()
            if ref_mode is None:
                return rules
            common = rules.split("## A. 通用", 1)[-1].split("## B. Ref2VA", 1)[0]
            mode = rules.split("## B. Ref2VA", 1)[-1].split("## C. 基础模式", 1)[0] if ref_mode else rules.split("## C. 基础模式", 1)[-1]
            return common + "\n" + mode
    except OSError:
        return ""


def compose_segment_prompt(
    project: Project,
    *,
    description: str,
    material_lines: list[str],
    duration: float = 10.0,
    ref_mode: bool = True,
    context: str = "",
) -> dict:
    """把一段中文口语描述写成 Ref2VA 的正文（detailed_description）。

    与 `prompt_engineer` 的分工：那个是给**已有分镜**填提示词的；这个是给
    「用户手选素材 + 手写一段话」的单段生成流程写正文。它拿得到素材编号表，
    所以能把 `<Subject N>` 直接写进正文 —— 这是参考模式一致性到位的关键。

    只产出**正文与音轨**；六段里的其余四段（字段名、编号声明、保留标记、负面约束）
    由 `ref_plan` 拼 —— 字段、编号、固定套路是代码的职责，不是模型的。

    返回正文、音轨、视觉保留标记与质量提醒。音轨也交给它写，是因为
    "这一段的底声是什么"和画面是同一件事的两面，分两次调用反而会脱节。

    镜头时间轴（`ref_plan.plan_shot_timeline`）由**代码**算好喂进去，不让模型自己排 ——
    它排出来的十有八九会把最后一镜贴到片尾。见 ref_plan 里那段说明。

    `ref_mode`（2026-09-19 加）：走哪套格式。
      True（Ref2VA）：正文用 `<Subject N>` 编号引用素材，之后由 ref_plan 拼六段式。
      False（基础模式 I2VA/FL2VA）：**不编号**，正文就是 `integrated_multimodal_description`，
        外壳与关键帧对齐指令由 `video_provider.compose_h3_prompt` 在出片时拼 ——
        两套格式是并列的，混着用会得到"引用了一堆没声明的标签"的畸形提示词。
    """
    from h3_prompt_policy import audit_body, reference_errors, reference_labels, retention_errors, shot_intent
    count, warnings = shot_intent(description, duration)
    timeline = ref_plan.plan_shot_timeline(duration, count=count)
    timeline_lines = []
    for idx, start, length in timeline:
        head = "0.000s 起（**这一颗不写时间戳**）" if idx == 1 \
            else f"At {ref_plan.format_timestamp(start)} 起"
        timeline_lines.append(f"  [Shot {idx}] {head}，这一颗约 {length:.1f} 秒")

    words = "350-500" if ref_mode else "120-170" if duration <= 5 else "200-280" if duration <= 10 else "280-380"

    style_en = str(getattr(project, "style_en", "") or getattr(project, "style", "")).strip()
    user = (
        f"这一段视频的总时长：{duration:g} 秒\n"
        f"镜头时间轴（**照抄，不要自己改**）：\n" + "\n".join(timeline_lines) + "\n"
        f"正文篇幅：{words} 个英文词左右\n"
        f"整体风格（英文，作为第一句 Style Lock 的素材）：{style_en or 'Live-action, cinematic'}\n\n"
        + ("素材清单（编号 -> 是什么）：\n" if ref_mode
           else "素材清单（**没有编号**，正文里用名字自然称呼即可）：\n")
        + "\n".join(f"  {line}" for line in material_lines) + "\n\n"
        f"当前视频描述（用户意图，不得被前情替代）：\n{description.strip()}\n"
        f"前情说明（仅文字背景，不是输入视频；不作为新增镜头指令）：\n{context.strip() or '无'}\n"
    )
    # system = 任务规则（本文件）+ 模式说明（基础模式才加）+ 运行时读进来的规则精要
    system = SEGMENT_SYSTEM if ref_mode else f"{SEGMENT_SYSTEM}\n\n{_BASE_MODE_NOTE}"
    rules = _prompt_rules(ref_mode)
    if rules:
        system = f"{system}\n\n---\n\n以下是本项目维护的 H3 提示词规则精要（与上面要求一致，冲突时以它为准）：\n\n{rules}"
    allowed = reference_labels("\n".join(material_lines)) if ref_mode else {"<Picture 1>"}
    retention_labels = set()
    if ref_mode:
        for line in material_lines:
            labels = reference_labels(line)
            subjects = {label for label in labels if label.startswith("<Subject ")}
            retention_labels.update(subjects or {label for label in labels if label.startswith(("<Picture ", "<Video "))})
    # One bounded repair for invalid model output; never submit a malformed video request.
    errors = []
    for attempt in range(2):
        repair = "" if not errors else "\n上次输出不合规，请修正以下问题并重新输出 JSON：\n" + "\n".join(errors)
        data = chat_json(system, user + repair, temperature=0.6)
        if not isinstance(data, dict):
            errors = ["输出必须是包含 video_prompt 和 soundscape 的 JSON 对象"]
            continue
        body = data.get("video_prompt", "")
        sound = data.get("soundscape", "")
        retention = data.get("retention", {})
        if not isinstance(body, str) or not isinstance(sound, str):
            errors = ["video_prompt 和 soundscape 必须是字符串"]
            continue
        audit = audit_body(body, duration, allowed=allowed, expected_shots=count)
        errors = audit.errors
        errors.extend(reference_errors(sound, allowed))
        errors.extend(retention_errors(retention, retention_labels))
        if not errors:
            if ref_mode and len(body.split()) < 350:
                warnings.append("编排正文较简短，建议确认动作、构图和参考关系是否描述完整。")
            if ref_mode and body.lstrip().startswith("[Shot 1]"):
                warnings.append("Ref2VA 正文未在首镜前单独说明整体风格，建议确认画面风格是否明确。")
            return {"video_prompt": body.strip(), "soundscape": sound.strip(),
                    "retention": retention,
                    "warnings": list(dict.fromkeys(warnings + audit.warnings))}
    raise ValueError("提示词校验失败，已停止视频提交：" + "；".join(dict.fromkeys(errors)))



OPTIMIZE_SYSTEM = """你是短片分镜师。用户会给你一段中文口语描述（可能有错别字、可能只有一句念头），
你要把它**用中文**写成一段能直接照着拍的画面描述 —— 用户要拿它核对"这是不是我想拍的东西"。

规则：

1. **输出中文**。不要输出英文，不要做任何翻译。用户只看中文。
2. 写成连贯的散文（可以按镜头分成几段），**不要加小标题、不要写成"1. 2. 3."式的条目清单**。
3. 未明确要求切镜时保持一镜到底，按提供的镜头数量写。每一颗镜头要写到这几样：
   - 画面里是谁 / 在哪 / 什么时间、什么光
   - 他/她在做什么（动作要有起止，不要只写状态）
   - **表演细节**：眉眼、嘴角、手、呼吸、肩背的松紧 —— 这是"像真的"和"像摆拍"的分界线
   - 镜头怎么走：景别 + 机位 + 运镜 + 幅度 + 速度
   - 这一颗结束时人和镜头停在哪
   ⚠️ 运镜请用下面这套说法（**写中文，括号里是官方术语** —— 下一步翻成英文时模型就照它落笔）：
     推近（Push In）/ 拉远（Pull Out）/ 变焦推近拉远（Zoom In/Out）/
     横摇（Pan Left/Right，机位不动只转头）/ 平移（Truck Left/Right，整机横移）/
     俯仰摇（Tilt Up/Down）/ 升降（Pedestal Up/Down）/ 绕拍（Arc Shot）/
     跟拍（Tracking Shot）/ 固定机位（Static Shot）/ 主观视角（POV）。
     幅度写"小幅/大幅"，速度写"缓慢/快速"；**中等幅度和常速就不写**（写了反而稀释）。
     运镜要写成句子里的动作（"镜头小幅缓慢推近她手里的信"），不要只在句末堆几个词。
   ⚠️ **切镜要有理由**：只有真的换主体、换空间、换状态、换视角或跳时间才切；只是
     想换个景别或稍微改个角度，就改用运镜，别切。
4. **只用我给的名字**（角色名 / 场景名 / 道具名）。没给的名字不要凭空造。
   也不要写「参考图 1」「<Picture 1>」这类编号 —— 编号是下一步的事，这里不归你管。
5. 不写台词以外的东西：不要字幕、水印、界面元素、配乐。画面里**本来就有的**文字
   （招牌、霓虹、横幅）要原样写出来、别改字。
6. 不要复述同一件事凑字数，把词花在细节上。

只输出 JSON：{"description": "..."}"""


def optimize_description(
    project: Project,
    *,
    description: str,
    material_names: list[str] | None = None,
    duration: float = 10.0,
) -> str:
    """把一段中文口语描述扩写成**中文**画面描述，给用户过目与修改。

    与 `compose_segment_prompt` 的分工（两步走，2026-09-19 斌哥定）：
      ① 本函数 —— 中文进、**中文出**，用户看得懂、能改
      ② `compose_segment_prompt` —— 中文进、英文 H3 正文出，**出片时**才跑

    为什么要分两步：英文六段式摆给用户看，他看不出"这是不是我想拍的东西"，改也无从改起。
    代价是点一次「让 AI 帮写」多花一次文本模型调用（DeepSeek，很便宜，不是出图那种日额度）。

    ⚠️ 这里**不注入素材编号**（`<Picture N>` / `<Subject N>`）—— 编号表由
    `ref_plan.build_ref_plan` 在出片时按当时选中的素材算，而两步之间用户还可能改选素材。
    给模型的是**名字**（角色「林晚」），照着写就行。
    """
    from h3_prompt_policy import shot_intent
    count, _ = shot_intent(description, duration)
    timeline = ref_plan.plan_shot_timeline(duration, count=count)
    # 中文字数大约是英文词数的 1.5~1.8 倍，跟 compose_segment_prompt 那三档对齐
    if duration <= 5:
        words = "200-280"
    elif duration <= 10:
        words = "320-450"
    else:
        words = "450-600"

    parts = [
        f"这一段视频的总时长：{duration:g} 秒，大约分成 {len(timeline)} 颗镜头。",
        f"篇幅：{words} 个中文字左右。",
    ]
    style_cn = str(getattr(project, "style", "") or "").strip()
    if style_cn:
        parts.append(f"整体风格：{style_cn}")
    if material_names:
        parts.append(
            "这一段能用的素材（**只用这些名字，别的不许编**）：\n"
            + "\n".join(f"  {line}" for line in material_names)
        )
    parts.append(f"用户描述（中文口语，可能有错别字，按意图理解）：\n{description.strip()}")
    data = chat_json(OPTIMIZE_SYSTEM, "\n".join(parts) + "\n", temperature=0.6)
    return str(data.get("description", "")).strip()


def next_block(
    project: Project,
    blocks: list,
    instruction: str = "",
) -> dict:
    """分块 Agent：产出下一个块（dict），或 {"done": True, "reason": ...} 表示故事已收尾。

    blocks 是已有的 Block 对象列表（按 block_id 升序）。只回传每块的剧情要素，
    不带提示词与文件路径——接力需要的是剧情衔接，不是上一块的实现细节。
    """
    chars = "\n".join(
        f"  - {c.name}：{c.anchor}" + (f"｜音色：{c.voice}" if c.voice else "")
        for c in project.characters
    )
    assets = [
        f"  - [道具] {a.name}：{a.anchor}" for a in project.assets if a.kind == "prop"
    ] + [f"  - [场景] {a.name}：{a.anchor}" for a in project.assets if a.kind == "scene"]

    if blocks:
        done_lines = []
        for b in sorted(blocks, key=lambda x: x.block_id):
            beats_brief = "；".join(
                f"{bb.start:g}-{bb.end:g}s {bb.action}" for bb in b.beats if bb.action
            )
            line = f"  第{b.block_id:02d}块：{b.summary}"
            if beats_brief:
                line += f"（{beats_brief}）"
            if b.dialogue:
                line += f" 台词：{b.dialogue}"
            done_lines.append(line)
        prev_part = "已完成块（按顺序，新块必须衔接最后一块）：\n" + "\n".join(done_lines)
    else:
        prev_part = "还没有任何块，这是第一块（continue_last 必须为 false，开场要建立空间与人物）。"

    user = (
        f"故事设定：\n"
        f"标题：{project.title}\n故事：{project.logline}\n风格：{project.style}\n"
        f"角色名单：\n{chars or '  （无）'}\n"
        + ("资产名单：\n" + "\n".join(assets) if assets else "资产名单：（无）")
        + f"\n\n{prev_part}"
        + (f"\n\n用户对这一块的指令：{instruction.strip()}" if instruction.strip() else "")
        + "\n\n请产出下一个块。"
    )
    data = chat_json(NEXT_BLOCK_SYSTEM, user, temperature=0.7)
    if data.get("done"):
        return {"done": True, "reason": str(data.get("reason", ""))}
    block = data.get("block")
    if not isinstance(block, dict) or not str(block.get("summary", "")).strip():
        raise RuntimeError("分块 Agent 未返回有效的块")
    return {"done": False, "block": block}


def block_prompts(project: Project, block) -> dict:
    """为单个块生成 visual_prompt / negative_prompt / video_prompt / audio。

    block 会被就地填充（传 Block 对象），同时返回 dict 方便接口层直接回传。
    音色一致性：角色名单里给出固定的 (S#) 映射与中文音色，要求逐字复用英文音色短语。
    """
    char_map = {
        c.name: (f"(S{i})", c.voice, c.anchor)
        for i, c in enumerate(project.characters, start=1)
    }
    chars = "\n".join(
        f"  - {c.name}：锚点英文版要原样拼进 visual_prompt｜音色：{c.voice or '未设定，按形象合理设计'}"
        f"｜说话人 ID：(S{i})"
        for i, c in enumerate(project.characters, start=1)
    )
    scene = project.asset_of(block.scene, "scene") if block.scene else None
    props = [project.asset_of(p, "prop") for p in block.props]
    beats = "\n".join(
        f"  {bb.start:g}-{bb.end:g}s：{bb.action}" + (f"｜镜头：{bb.camera}" if bb.camera else "")
        for bb in block.beats
    )
    speakers = "、".join(
        f"{n}={char_map[n][0]}" for n in block.characters if n in char_map
    ) or "（本块无对白角色）"

    scene_part = (
        f"场景：{scene.name}｜{scene.anchor}" if scene else "场景：无明确环境"
    )
    user = (
        f"全局风格：{project.style}\n"
        f"角色名单（说话人 ID 全片固定，不要重新编号）：\n{chars}\n"
        f"本块说话人：{speakers}\n"
        f"{scene_part}"
    )
    if props:
        user += "\n画面里的道具：" + "；".join(f"{p.name}（{p.anchor}）" for p in props if p)
    user += f"\n主景别：{block.camera or '未指定'}｜主运镜：{block.motion or '未指定'}\n"
    if beats:
        user += f"分秒节拍（video_prompt 必须带时间锚逐段展开）：\n{beats}\n"
    if block.dialogue:
        user += f"台词/旁白（原样保留进 [语言标签]，不翻译）：\n{block.dialogue}\n"
    if block.avoid:
        user += f"画面禁忌（翻成 Avoid: 英文短句放正文末尾）：{block.avoid}\n"
    data = chat_json(BLOCK_PROMPT_SYSTEM, user, temperature=0.5)

    out = {
        "visual_prompt": str(data.get("visual_prompt", "")),
        "negative_prompt": str(data.get("negative_prompt", "")),
        "video_prompt": str(data.get("video_prompt", "")),
        "audio": str(data.get("audio", "")),
    }
    if not out["visual_prompt"]:
        raise RuntimeError("块提示词 Agent 未返回 visual_prompt")
    if not out["video_prompt"]:
        raise RuntimeError("块提示词 Agent 未返回 video_prompt")
    block.visual_prompt = out["visual_prompt"]
    block.negative_prompt = out["negative_prompt"]
    block.video_prompt = out["video_prompt"]
    if out["audio"]:
        block.audio = out["audio"]
    return out
    """阶段三：就地填充每个分镜的 visual_prompt / negative_prompt / video_prompt。

    两种提示词在同一路调用里产出，而不是拆成两个 Agent：
    visual_prompt 给图像模型出首帧图，video_prompt 配首帧图给图生视频模型。
    拆开会让同一份分镜上下文重复发送一遍，没必要。
    """
    lines = []
    for s in shots:
        extra = (
            f" | 出现道具：{', '.join(s.prop_refs)}" if s.prop_refs else ""
        ) + (
            f" | 所在场景：{', '.join(s.scene_refs)}" if s.scene_refs else ""
        )
        lines.append(
            f"[{s.shot_id}] 画面：{s.scene_desc} | 景别：{s.camera} | 运镜：{s.motion}"
            f" | 时长：{s.duration:g}s | 台词：{s.dialogue or '无'}"
            f" | 出场角色：{', '.join(s.character_refs) or '无'}{extra}"
        )

    anchors = "\n".join(
        f"  - {c.name}：{c.anchor}"
        + (f"｜音色：{c.voice}" if c.voice else "")
        + f"｜说话人 ID：(S{i})"
        for i, c in enumerate(project.characters, start=1)
    )
    user = (
        f"全局风格：{project.style}\n"
        f"角色锚点（说话人 ID 已固定，所有分镜必须沿用同一映射，不要重新编号）：\n{anchors}\n\n"
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
