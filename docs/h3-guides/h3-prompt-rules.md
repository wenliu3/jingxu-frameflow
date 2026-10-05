# H3 提示词规则精要（本项目的运行时规则源）

这份文件是**我们自己整理的规则精要**，由后端在拼提示词时**运行时读入**（`agents._prompt_rules()`），
拼进写正文那个 agent 的 system prompt。

**为什么要单独一份文件**：规则集中在这里，官方更新时只改这一个文件，不用翻代码；
人和 AI 也都能直接查同一份。

> ⚠️ **不是官方原文**。官方那份（`MiniMax-AI/MiniMax-H3` → `skills/h3-prompt-writing/`，含
> `SKILL.md`、`references/base-en.txt`、`references/ref-en.txt`）采用的是 **MiniMax H3
> Community License**（厂商自定义协议，不是 MIT/Apache），把原文整份拷进本仓库不合适。
> 所以这里是**要点转述 + 我们的工程结论**。
>
> 官方来源（2026-10-04 核对；按 Ref2VA 与基础模式分别加载）：
> - Skill 入口：https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/SKILL.md
> - 基础模式指南（T2VA/I2VA/FL2VA/L2VA）：`.../skills/h3-prompt-writing/references/base-en.txt`
> - 全参考模式指南（Ref2VA）：`.../skills/h3-prompt-writing/references/ref-en.txt`
> - 安装成 agent skill：`npx skills add https://github.com/MiniMax-AI/MiniMax-H3 --skill h3-prompt-writing`
>
> **更新时核对这些点**（官方改了哪条就改哪条）：运镜词表、切镜用词、说话人/对白写法、
> 六段式字段名与顺序、retention 标记词、summary 任务标记、对齐指令句式、篇幅区间。

---

## A. 通用（两种模式都适用）

1. **镜头结构**：`[Shot 1]` **不带时间戳**（后面直接接风格与初始构图）；第 2 颗起写成
   `[Shot N] At MM:SS.mmm, ...`，切点时间必须**严格递增**且落在视频时长内。
2. **风格开场**：Ref2VA 在 `[Shot 1]` 前用 1–2 句建立影像质感；基础模式在 `[Shot 1]` 后建立影像质感（介质/胶片感、画幅、光源与方向、色调、材质、氛围）。
   可以点名风格（live-action / 3D CG / claymation…），但**必须紧跟具体细节**；
   只写 cinematic / beautiful / epic 这种空词等于没写。
3. **每颗镜头都要写全这七样**：① 当前构图 ② 主体外观与位置 ③ 环境与光照 ④ 动作与状态变化
   ⑤ 运镜 ⑥ 当前声音 ⑦ 参考内容实际出现/生效的点。不要写成剧情摘要。
4. **运镜 = 类型 + 幅度 + 速度**，写成句子里的自然动作，不许把标签堆在句末：
   - 类型：Zoom In/Out（机身不动只改焦距）、Push In/Pull Out（前推/后拉）、Pan Left/Right（机位不动、
     镜头水平摇）、Truck Left/Right（整机水平平移）、Tilt Up/Down（机位不动、镜头垂直摇）、
     Pedestal Up/Down（整机升降）、Arc Shot（绕主体弧线）、Tracking Shot（跟拍）、
     Static Shot（机位与镜头都静止）、POV、Roll Clockwise/Counterclockwise。
   - 幅度：`with small amplitude` / `with large amplitude`；速度：`at slow speed` / `at fast speed`。
   - **中等幅度和常速省略不写**（写了反而稀释重点）。
5. **切镜要有理由**：用 `the camera cuts to` / `the shot cuts to` / `the shot transitions to`；
   切点必须带来**新信息**（主体、空间、状态、视角或时间）。只想换景别或轻微改角度 → 用运镜，别切。
   普通切换不要写 cross-dissolve / fade / wipe（除非用户明确要求）。
6. **说话人与对白**：实际发声的主体给稳定 ID `(S1)`/`(S2)`（齐说写 `(S1,S2)`）；
   **同一人跨镜头同一 ID，从不发声的角色不给 ID**。说话人的身份/动作/演绎写在 `<d>` **外面**，
   `<d>` 内只放语言标签 + 用户给的口语内容，**逐字保留、不翻译不改标点**：
   `The young woman (S1) says: <d>[Chinese] 我在下一站下车。</d>`
7. **画外音**：必须写 `says in an off-screen voiceover`，并在同一句里说明该角色**嘴唇全程闭合**。
8. **画面里实际可见的文字**（招牌、霓虹、横幅）用双引号原样保留、不翻译：
   `a red neon sign reading "营业中"`。（这与"禁止加上去的字幕/水印/logo"不冲突。）
9. **`overall_soundscape`**：1–4 句，只写全片环境声与物理音（风雨、脚步、布料、呼吸…），
   **不重复对白/歌唱/画内音乐**；只有整片完全静音时才写 `N/A`。
10. **`non_diegetic_music`**：1–3 句，写角色听不到、只有观众能听的配乐，聚焦配器/速度/节奏/动态；
    **禁止抽象情绪词、禁止解释情感功能**；没有就写 `N/A`。
    角色能听到的音乐属于画内事件，写进正文，不写这里。
11. **篇幅**：Ref2VA 生成正文通常 350–500 英文词（对白密集时优先把口播时间线写全，别灌水）。

## B. Ref2VA（全参考模式）专属

工程约束：用户未要求切镜时保持单镜头；明确提出切镜时才分镜。角色只在当前描述要求的镜头出现。
引用图是身份/外观基线，不覆盖用户明确要求的换衣、光线、背景或幻想效果。不得虚构未提供的视频或音频参考。


1. **六段式，顺序不能换、字段名照抄**：
   `subject_definitions` → `summary` → `retention_analysis` → `detailed_description`
   → `overall_soundscape` → `non_diegetic_music`。
   **六段式本身就是完整提示词** —— 不要再套基础模式的三段壳，也**没有**关键帧对齐那一行
   （语义参考本身不占时间轴；本项目续拍另外用 AddGuide 引导第0帧）。
2. **标签**（一旦分配，全部段落里含义一致；不得出现没被定义的标签）：
   - `<Subject N>`：从素材里抽象出的**可复用可见内容**（人物/物体/场景/服装/风格/动作）。
   - `<Picture N>`：作为**具体帧**（首帧/关键帧/尾帧/构图锚点）的参考图才单独成条；
     **仅用于定义角色/场景/服装/风格的图不要单列**，在对应 `<Subject N>` 定义里内嵌引用即可。
   - `<Audio N>`：被复制或参考的音频。
   - 写法：`<Subject N> is the <身份> shown in <Picture N>: <锚点短语>.`（用冒号直接接完整名词短语）。
3. **`summary`**：以方括号任务类型开头，多个用 ` + ` 连接、同类型不重复：
   `[reference generation]`（提供生成指导）/ `keyframe completion`（图作具体帧行）/
   `audio reference`（只借音色/风格）/ `audio reuse`（原音频被复用）/ `video editing` /
   `video continuation`。**summary 里不得引入新标签。**
4. **`retention_analysis`**：每个标签一行 + 标记词，**两套标记词不能混**：
   - 视觉（`<Subject>`/`<Picture>`/`<Video>`）：`fully_preserved` / `partially_preserved` /
     `attribute_transfer` / `weak_reference`
   - 音频（`<Audio>`）：`fully_copy` / `partially_copy` / `reference` / `weak_reference`
   - 本项目只借音色 → 用 `reference`，并写死 `do not reuse its spoken content as dialogue`
     （这是防音色样本内容泄漏成台词的那道闸）。这一段**不写 (Sx)**。
5. **`detailed_description`**：逐镜写，并在素材首次出现/生效处插入标签；
   说话人同时保留视觉标签与说话人 ID：`<Subject 2> (S1) says, <d>...</d>`。

本项目的真实视频输入：清单提供 `<Video N>` 时，H3 会接收成功成片的选定帧批次，可引用其动作、构图和外观；文本编排器没有看过视频，不能编造视频分析结果。引用主体必须以用户描述和已提供锚点为依据。续拍的尾帧通过 AddGuide 固定为第0帧，不因它存在就虚构 `<Picture N>`；只写新片动作，不重播前情。来源音频不作为参考输入。

## C. 基础模式（T2VA / I2VA / FL2VA / L2VA）专属

1. **Part One：关键帧对齐指令（提示词第一行，且只在这一模式里有）**
   - I2VA：`For the target video, at 0.00 seconds into the target video, [Picture 1] (from [Shot 1]) is fully referenced.`
   - FL2VA：`How the reference pictures align with the target video - Picture 1 (from [Shot 1]) aligns with the 0.00-second mark of the target video; Picture 2 (from [Shot N]) aligns with the S.SS-second mark of the target video.`
     （`S.SS` = 实际时长，**恰好两位小数**。）
   - 这两句**由代码生成**（`video_provider.compose_h3_prompt`），不要交给文本模型写 ——
     它依赖吸附后的帧数，模型看不到那个参数。
2. **Part Two：三个字段**：`integrated_multimodal_description`（正文）→ `overall_soundscape`
   → `non_diegetic_music`。
3. **不要用 `<Subject N>` 编号**（那是 Ref2VA 的标签体系）：基础模式用自然语言点名主体，
   首帧那张图可以用 `<Picture 1>` 指代。
4. **关键帧写法**：I2VA 从首帧"向前发展"（首帧锚定 → 动作起势 → 连续发展 → 结果/反应）；
   FL2VA 写"首帧 → 中间变化 → 差异收窄 → 末帧"的路径，**通常偏好单镜头**；
   L2VA 先建立合理前置状态、最后收敛到末帧。
