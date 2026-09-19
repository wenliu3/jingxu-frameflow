"""音频生成通道（角色音色样本）。

**用途只是造音色样本**：产出 2-15 秒干净人声，喂给 Ref2VA 的参考音频槽位，
让 H3 在整片里用同一个音色说话。
环境音 / 配乐不归这里 —— 那是 H3 的 `overall_soundscape` / `non_diegetic_music`
原生生成的，本来就和画面同源出。

## 两个后端（2026-09-14 起可切换）

| 后端 | 音色池 | 成本 | 特点 |
| --- | --- | --- | --- |
| `edge`（默认） | 8 个（标准普通话 6 个） | **免费** | 零显存、零成本；但音色只能"从池子里挑最近的"，没有声音设计 |
| `minimax` | 27 个中文音色 | 3.5 元/万字符（speech-2.8-hd） | 音色按角色气质分类（御姐/少女/俊朗男友/冷淡学长…），32kHz 输出与 H3 音频 VAE 同采样率 |

后端由 `AUDIO_PROVIDER` 决定（服务配置面板下发的 `audio_provider`）。
`Character.tts_voice` 存的是**当前后端**的音色 id —— 换后端时旧 id 在新池子里
找不到，`assign_voices` 会按 voice 描述重挑（见 `_resolve_pool`）。

## 为什么不用魔搭

魔搭 API 侧没有任何音频能力（2026-09-14 实测：`/v1/models` 49 个在服务模型里音频类为 0；
经典任务接口对整个模型库都返回 401 record not found，路由已下线；`/v1/audio/speech` 是 404）。
所以音频只能走自建后端 —— edge-tts（本地调微软在线接口）或 MiniMax 开放平台。

## 时长为什么是 5 秒

H3 对参考音频的限制是「每段 2-15 秒，**且 3 段总时长 ≤15 秒**」。所以单段时长被段数压缩：

| 角色数 | 每段可用 | 说明 |
| --- | --- | --- |
| 1 | ≤15 秒 | |
| 2 | ≤7.5 秒 | |
| **3** | **≤5 秒** | 3×5=15 正好用满，最常见的配置 |

`SAMPLE_TEXT` 定在 20 字 ≈ 5 秒就是为这个。**想加长之前先看角色数**。
"""

from __future__ import annotations

import asyncio
import os
from collections import Counter

# ---------------------------------------------------------------- 后端选择
DEFAULT_PROVIDER = "edge"
DEFAULT_MINIMAX_BASE = "https://api.minimaxi.com/v1"
DEFAULT_MINIMAX_MODEL = "speech-2.8-hd"
# MiniMax 的音频输出采样率，与 H3 的 audio VAE 一致 —— 少一次重采样
MINIMAX_SAMPLE_RATE = 32000


def current_provider() -> str:
    return (os.getenv("AUDIO_PROVIDER") or DEFAULT_PROVIDER).strip().lower()


# ---------------------------------------------------------------- 音色池
# 每个音色带 keys：用于按 Character.voice 的文字描述匹配。
# 用「关键词打分」而不是 if-else 链，是因为加音色时只要补 keys，不用动匹配逻辑。
VOICES_EDGE: tuple[dict, ...] = (
    {
        "id": "zh-CN-XiaoxiaoNeural", "cn": "晓晓", "gender": "female", "locale": "普通话",
        "hint": "温柔知性的成年女声，语速平稳。适合旁白、成熟女性、母亲类角色",
        "keys": ("温柔", "知性", "温和", "平静", "淡定", "成年女", "成熟女", "母亲", "妈妈", "旁白"),
    },
    {
        "id": "zh-CN-XiaoyiNeural", "cn": "晓伊", "gender": "female", "locale": "普通话",
        "hint": "清亮偏年轻的少女音，语速轻快。适合少女、活泼或俏皮的角色",
        "keys": ("少女", "甜", "活泼", "俏皮", "清脆", "清亮", "年轻女", "元气", "萝莉"),
    },
    {
        "id": "zh-CN-YunxiNeural", "cn": "云希", "gender": "male", "locale": "普通话",
        "hint": "清朗的青年男声，语调平稳。适合少年感、青年主角",
        "keys": ("青年男", "少年感", "清朗", "温和男", "年轻男", "大学生"),
    },
    {
        "id": "zh-CN-YunjianNeural", "cn": "云健", "gender": "male", "locale": "普通话",
        "hint": "厚实有力的成年男声，语气沉稳。适合成熟、硬朗、威严或中老年男性",
        "keys": ("低沉", "沙哑", "浑厚", "磁性", "稳重", "沉稳", "中年男", "大叔", "苍老",
                 "老者", "老年男", "硬朗", "威严", "庄重", "压迫"),
    },
    {
        "id": "zh-CN-YunxiaNeural", "cn": "云夏", "gender": "male", "locale": "普通话",
        "hint": "偏年轻的少年男声，语速稍快。适合少年、孩子",
        "keys": ("少年", "孩童", "孩子", "稚", "正太", "男童"),
    },
    {
        "id": "zh-CN-YunyangNeural", "cn": "云扬", "gender": "male", "locale": "普通话",
        "hint": "标准播音腔男声，字正腔圆。适合讲述者、旁白、播报式角色",
        "keys": ("播音", "播报", "字正腔圆", "讲述", "解说", "新闻", "主持人"),
    },
    {
        "id": "zh-CN-liaoning-XiaobeiNeural", "cn": "小北", "gender": "female", "locale": "辽宁话",
        "hint": "辽宁口音女声，带东北方言味。只在角色设定明确要求东北口音时用",
        "keys": ("东北", "辽宁", "东北话", "方言女"),
    },
    {
        "id": "zh-CN-shaanxi-XiaoniNeural", "cn": "小妮", "gender": "female", "locale": "陕西话",
        "hint": "陕西口音女声，带西北方言味。只在角色设定明确要求西北口音时用",
        "keys": ("陕西", "西北", "陕西话", "方言女"),
    },
)

# MiniMax 系统音色（v1/v2 可用）。既有 14 个基础音色，也有 v2 新增的角色化音色 ——
# 后者按「气质标签」命名，跟"给角色配音"的匹配度比 edge 那套高得多。
VOICES_MINIMAX: tuple[dict, ...] = (
    {
        "id": "female-shaonv", "cn": "少女", "gender": "female", "locale": "普通话",
        "hint": "标准少女音色", "keys": ("少女", "清亮", "年轻女", "元气"),
    },
    {
        "id": "female-yujie", "cn": "御姐", "gender": "female", "locale": "普通话",
        "hint": "成熟有气场的御姐音", "keys": ("御姐", "成熟女", "气场", "冷艳"),
    },
    {
        "id": "female-chengshu", "cn": "成熟女性", "gender": "female", "locale": "普通话",
        "hint": "成熟稳重的中年女声",
        "keys": ("成熟女", "中年女", "稳重", "沉稳女", "母亲", "妈妈", "偏慢", "语速慢", "舒缓"),
    },
    {
        "id": "female-tianmei", "cn": "甜美女性", "gender": "female", "locale": "普通话",
        "hint": "甜美温柔的女声",
        "keys": ("甜美", "温柔", "甜", "软", "嗲", "温和", "亲切", "温暖"),
    },
    {
        "id": "wumei_yujie", "cn": "妩媚御姐", "gender": "female", "locale": "普通话",
        "hint": "妩媚撩人的御姐音", "keys": ("妩媚", "撩", "妖", "艳丽"),
    },
    {
        "id": "tianxin_xiaoling", "cn": "甜心小玲", "gender": "female", "locale": "普通话",
        "hint": "甜心系少女音", "keys": ("甜心", "可爱女", "萌", "天真女"),
    },
    {
        "id": "qiaopi_mengmei", "cn": "俏皮萌妹", "gender": "female", "locale": "普通话",
        "hint": "俏皮活泼的萌妹音", "keys": ("俏皮", "萌", "活泼", "跳脱", "鬼马"),
    },
    {
        "id": "diadia_xuemei", "cn": "嗲嗲学妹", "gender": "female", "locale": "普通话",
        "hint": "嗲声嗲气的学妹音", "keys": ("嗲", "学妹", "撒娇", "少女感"),
    },
    {
        "id": "danya_xuejie", "cn": "淡雅学姐", "gender": "female", "locale": "普通话",
        "hint": "淡雅清冷的学姐音", "keys": ("淡雅", "清冷", "学姐", "知性", "冷静女"),
    },
    {
        "id": "lovely_girl", "cn": "萌萌女童", "gender": "female", "locale": "普通话",
        "hint": "小女孩音色", "keys": ("女童", "小女孩", "儿童女", "孩子女", "幼"),
    },
    {
        "id": "presenter_female", "cn": "女性主持人", "gender": "female", "locale": "普通话",
        "hint": "播音腔女声", "keys": ("女主持", "播音女", "播报女", "解说女", "新闻女"),
    },
    {
        "id": "audiobook_female_1", "cn": "女性有声书1", "gender": "female", "locale": "普通话",
        "hint": "娓娓道来的讲述女声", "keys": ("讲述", "旁白女", "有声书", "娓娓"),
    },
    {
        "id": "audiobook_female_2", "cn": "女性有声书2", "gender": "female", "locale": "普通话",
        "hint": "另一种讲述女声（偏沉）",
        "keys": ("旁白女", "沉稳女", "低沉女", "中性女", "中性", "偏低", "偏沉", "冷淡女"),
    },
    {
        "id": "male-qn-qingse", "cn": "青涩青年", "gender": "male", "locale": "普通话",
        "hint": "年轻男生的青涩感", "keys": ("青涩", "年轻男", "少年感", "单纯", "初出"),
    },
    {
        "id": "male-qn-jingying", "cn": "精英青年", "gender": "male", "locale": "普通话",
        "hint": "干练克制的精英男声", "keys": ("精英", "干练", "克制", "冷静", "职场男", "理性"),
    },
    {
        "id": "male-qn-badao", "cn": "霸道青年", "gender": "male", "locale": "普通话",
        "hint": "霸道强势的青年男声", "keys": ("霸道", "强势", "压迫", "强硬", "霸气"),
    },
    {
        "id": "male-qn-daxuesheng", "cn": "青年大学生", "gender": "male", "locale": "普通话",
        "hint": "大学生气质的男声", "keys": ("大学生", "学生男", "青年男", "清朗男"),
    },
    {
        "id": "junlang_nanyou", "cn": "俊朗男友", "gender": "male", "locale": "普通话",
        "hint": "俊朗温柔的男友音", "keys": ("俊朗", "温柔男", "男友", "暖男", "清俊"),
    },
    {
        "id": "lengdan_xiongzhang", "cn": "冷淡学长", "gender": "male", "locale": "普通话",
        "hint": "冷淡疏离的学长音", "keys": ("冷淡", "疏离", "学长", "清冷男", "淡漠"),
    },
    {
        "id": "chunzhen_xuedi", "cn": "纯真学弟", "gender": "male", "locale": "普通话",
        "hint": "纯真少年感的学弟音", "keys": ("纯真", "学弟", "少年", "青涩男", "干净男"),
    },
    {
        "id": "badao_shaoye", "cn": "霸道少爷", "gender": "male", "locale": "普通话",
        "hint": "骄纵的少爷音", "keys": ("少爷", "骄纵", "纨绔", "矜贵"),
    },
    {
        "id": "bingjiao_didi", "cn": "病娇弟弟", "gender": "male", "locale": "普通话",
        "hint": "病娇感的少年音", "keys": ("病娇", "弟弟", "偏执", "阴郁少年"),
    },
    {
        "id": "clever_boy", "cn": "聪明男童", "gender": "male", "locale": "普通话",
        "hint": "机灵的男童音", "keys": ("男童", "小男孩", "儿童男", "孩子男", "机智"),
    },
    {
        "id": "cute_boy", "cn": "可爱男童", "gender": "male", "locale": "普通话",
        "hint": "软萌的男童音", "keys": ("男童", "可爱男", "软萌", "幼男"),
    },
    {
        "id": "presenter_male", "cn": "男性主持人", "gender": "male", "locale": "普通话",
        "hint": "播音腔男声", "keys": ("男主持", "播音", "播报", "字正腔圆", "解说", "新闻"),
    },
    {
        "id": "audiobook_male_1", "cn": "男性有声书1", "gender": "male", "locale": "普通话",
        "hint": "沉稳的讲述男声", "keys": ("讲述", "旁白男", "有声书", "叙事", "沉稳男"),
    },
    {
        "id": "audiobook_male_2", "cn": "男性有声书2", "gender": "male", "locale": "普通话",
        "hint": "低沉的讲述男声", "keys": ("低沉", "沙哑", "浑厚", "磁性", "苍老", "老者",
                                           "老年男", "威严", "庄重", "旁白男"),
    },
)

VOICES_BY_PROVIDER: dict[str, tuple[dict, ...]] = {
    "edge": VOICES_EDGE,
    "minimax": VOICES_MINIMAX,
}


def _resolve_pool(pool=None) -> tuple[dict, ...]:
    """取音色池。调用方可显式传（测试用），否则按当前后端。"""
    if pool is not None:
        return tuple(pool)
    return VOICES_BY_PROVIDER.get(current_provider(), VOICES_EDGE)


def pool_ids(pool=None) -> tuple[str, ...]:
    """当前池子里可自动分配的 id（自动分配只在标准普通话里挑，方言/特殊音色要人工指定）。

    edge 池里带 locale 字段，方言排除；minimax 池全都是普通话，不排除。
    """
    return tuple(
        v["id"] for v in _resolve_pool(pool) if v.get("locale", "普通话") == "普通话"
    )


def default_voice(pool=None) -> str:
    """当前池子的兜底音色（只用于"什么都判断不出来"的极端情况）。"""
    ids = pool_ids(pool)
    if ids:
        return ids[0]
    entries = _resolve_pool(pool)
    return entries[0]["id"] if entries else ""


def voice_label(voice_id: str, pool=None) -> str:
    """音色 id -> 中文名（界面显示用）。认不出就返回原 id。"""
    for v in _resolve_pool(pool):
        if v["id"] == voice_id:
            return v["cn"]
    return voice_id or ""


def voice_gender(voice_id: str, pool=None) -> str:
    for v in _resolve_pool(pool):
        if v["id"] == voice_id:
            return v["gender"]
    return ""


def knows_voice(voice_id: str, pool=None) -> bool:
    """这个 id 在不在当前池子里。换后端后旧 id 会失效，调用方据此重挑。"""
    return any(v["id"] == voice_id for v in _resolve_pool(pool))


# ---------------------------------------------------------------- 按描述挑音色
# 先判性别再算关键词得分。顺序不能反：「低沉磁性的中年女声」若先算关键词，
# "低沉"会把男声音色顶到最高分。
# ⚠️ 词表必须覆盖**音色名本身的角色标签**（学长/御姐/萌妹/少爷…）——
# MiniMax 的音色就是用这些词命名的，而它们大多不含"男"/"女"字。
# 实测踩过：「冷淡疏离的学长音」因为"学长"不在表里而判不出性别，
# 于是退回轮转、拿了一个明显不匹配的音色。
_FEMALE_MARK = (
    "女", "少女", "女孩", "姑娘", "姐姐", "妈妈", "母亲", "阿姨", "奶奶", "她",
    "学妹", "学姐", "御姐", "萌妹", "小玲",
)
_MALE_MARK = (
    "男", "少年", "男孩", "哥哥", "父亲", "爸爸", "叔叔", "爷爷", "大叔", "老者",
    "老翁", "老伯", "他",
    "学长", "学弟", "少爷", "弟弟", "兄长",
)


def _gender_of_desc(text: str) -> str:
    female = any(k in text for k in _FEMALE_MARK)
    male = any(k in text for k in _MALE_MARK)
    if female and not male:
        return "female"
    if male and not female:
        return "male"
    return ""


def _score(entry: dict, text: str) -> int:
    return sum(1 for k in entry.get("keys", ()) if k in text)


def _best_score(desc: str, pool=None) -> int:
    """这段描述在当前池子里能拿到的最高匹配分。0 = 一个关键词都没命中。"""
    t = (desc or "").strip()
    if not t:
        return 0
    return max((_score(v, t) for v in _resolve_pool(pool)), default=0)


def pick_voice(voice_desc: str, pool=None) -> str:
    """按 `Character.voice` 的文字描述挑一个最接近的音色。

    判不出性别时返回空串，交给 `assign_voices` 的轮转逻辑兜底 ——
    硬猜性别比留空更糟（会给一个明显不符的音色，而且看不出来是猜的）。
    """
    t = (voice_desc or "").strip()
    if not t:
        return ""
    gender = _gender_of_desc(t)
    if not gender:
        return ""
    candidates = [v for v in _resolve_pool(pool) if v["gender"] == gender]
    if not candidates:
        return ""
    # 命中关键词最多的胜出；平票时保持池中顺序（先写的更"通用"）
    best = max(candidates, key=lambda v: _score(v, t))
    return best["id"] if _score(best, t) > 0 else candidates[0]["id"]


def _attr(obj, key: str, default: str = "") -> str:
    """既吃 dataclass（pipeline 用）也吃 dict（server 直接操作 project.json 时用）。"""
    value = obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)
    return str(value or default).strip()


def assign_voices(characters, pool=None) -> dict[str, str]:
    """给一组角色分配音色，返回 {角色名: 音色 id}。

    规则：
    1. **首选排第一位** —— 否则"少年男声"会被同性别池的默认顺序送到别处去（实测踩过）
    2. 撞车就往后轮转 —— 一部片子两个女主用同一个音色，观众分不出谁在说话
    3. **同性别池用完时宁可重复同性别，也不跨性别** —— 女主配男声是硬错，
       撞音色只是不好区分。重复时挑被占用次数最少的
    4. 已指定的音色（tts_voice 非空**且在池子里**）优先占位；不在池子里说明换了后端，
       按描述重挑（这就是 "切后端后自动迁移音色" 的实现）
    """
    entries = _resolve_pool(pool)
    taken: set[str] = set()
    result: dict[str, str] = {}
    pending: list = []

    for c in characters:
        forced = _attr(c, "tts_voice")
        # 旧后端的音色 id 在新池子里不存在 → 当作未指定，重挑
        if forced and any(v["id"] == forced for v in entries):
            result[_attr(c, "name")] = forced
            taken.add(forced)
        else:
            pending.append(c)

    # 按匹配度从高到低处理：匹配准的角色先占位。
    # 不排的话，「一个关键词都不命中」的角色会先跑、把池里的第一个音色占走
    # （实测踩过：模糊描述的角色占掉了「少女」，真正的少女角色被挤到「御姐」）。
    # Python 的 sort 稳定，同分角色保持原顺序。
    pending.sort(key=lambda c: -_best_score(_attr(c, "voice"), entries))

    for c in pending:
        name = _attr(c, "name")
        want = pick_voice(_attr(c, "voice"), entries)
        gender = voice_gender(want, entries) if want else ""
        pool_ids_ordered: list[str] = []
        for vid in ([want] if want else []) + [v["id"] for v in entries]:
            if vid and vid not in pool_ids_ordered:
                pool_ids_ordered.append(vid)
        same = [v for v in pool_ids_ordered if gender and voice_gender(v, entries) == gender]
        others = [v for v in pool_ids_ordered if v not in same]

        choice = next((v for v in same if v not in taken), "")
        if not choice and same:
            count = Counter(result.values())
            choice = min(same, key=lambda v: (count[v], same.index(v)))
        if not choice:
            choice = next((v for v in others if v not in taken), "")
        result[name] = choice or want or default_voice(entries)
        taken.add(result[name])
    return result


# ---------------------------------------------------------------- 样本文本
# 四个条件，改之前先看：
#   ① **官方对 Ref2VA 参考音频的时长要求是每段 2-15 秒**（且 3 段总时长也 ≤15 秒）
#      —— 中文约 4 字/秒，20 字 ≈ 5 秒，为"3 个角色"这个最常见配置留出正好 15 秒
#   ② 干净、无杂音、无生僻字（读错字会让样本带噪声）
#   ③ **不能是剧情台词** —— 否则模型会把样本内容泄漏成台词。
#      防泄漏主要靠 Ref2VA 的 `voice only` 语法，但文本本身避开人名与情节更稳
#   ④ 语气自然（像正常说话的气口），模型学音色时才拿得到韵律信息
SAMPLE_TEXT = "山谷里的风停了，远处的灯一盏一盏亮起来。"


def sample_filename(name: str) -> str:
    """样本文件名。**只存文件名不存绝对路径** —— 换机器/挪目录就不会失效，
    与 shots 的 image_path 同一套做法（见 server/app.py 的 _load_task_from_disk）。
    """
    from pipeline import _safe_char_name  # 局部导入：pipeline 顶层会 import agents，避免环

    return f"voice_{_safe_char_name(name)}.mp3"


# ---------------------------------------------------------------- 合成
def _synth_edge(text: str, voice_id: str, out_path: str) -> str:
    import edge_tts  # 延迟导入：没装 edge-tts 时只有走到这一步才报错

    async def _run() -> None:
        await edge_tts.Communicate(text, voice_id).save(out_path)

    asyncio.run(_run())
    return out_path


def _synth_minimax(text: str, voice_id: str, out_path: str) -> str:
    """MiniMax 同步语音合成（`POST /v1/t2a_v2`）。

    协议要点（2026-09-14 查证官方文档）：
    - 鉴权是 `Authorization: Bearer <API Key>`，国内端点 `https://api.minimaxi.com/v1`
    - `output_format: "hex"` → `data.audio` 是 hex 字符串；也可能给 url（本函数两种都收）
    - `extra_info.usage_characters` 是**实际计费字符数**，打印出来便于对账
    - 采样率选 32000，与 H3 的 audio VAE 一致
    """
    import requests  # 与项目其它 provider 一致，用通用写法

    base = (os.getenv("AUDIO_BASE_URL") or DEFAULT_MINIMAX_BASE).strip().rstrip("/")
    key = (os.getenv("AUDIO_API_KEY") or "").strip()
    if not key:
        raise RuntimeError(
            "缺 MiniMax 的 API Key：请在「服务配置 → 音频模型」里填 audio_api_key"
        )
    model = (os.getenv("AUDIO_MODEL") or DEFAULT_MINIMAX_MODEL).strip()
    payload = {
        "model": model,
        "text": text,
        "stream": False,
        "voice_setting": {
            "voice_id": voice_id or default_voice(),
            "speed": 1,
            "vol": 1,
            "pitch": 0,
        },
        "audio_setting": {
            "sample_rate": MINIMAX_SAMPLE_RATE,
            "bitrate": 128000,
            "format": "mp3",
            "channel": 1,
        },
        "output_format": "hex",
    }
    resp = requests.request(
        "post",
        f"{base}/t2a_v2",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=payload,
        timeout=180,
    )
    resp.raise_for_status()
    data = resp.json()
    base_resp = data.get("base_resp") or {}
    code = base_resp.get("status_code")
    if code not in (0, None):
        raise RuntimeError(f"MiniMax 返回错误 {code}：{base_resp.get('status_msg')}")

    audio = (data.get("data") or {}).get("audio") or ""
    if not audio:
        raise RuntimeError(f"MiniMax 未返回音频：{str(data)[:300]}")
    info = data.get("extra_info") or {}
    if info.get("usage_characters") is not None:
        print(f"      MiniMax 计费字符：{info['usage_characters']}")

    if str(audio).startswith("http"):
        raw = requests.request("get", audio, timeout=120).content
    else:
        raw = bytes.fromhex(str(audio))
    with open(out_path, "wb") as fh:
        fh.write(raw)
    return out_path


def synth(text: str, voice_id: str, out_path: str) -> str:
    """按当前后端合成一段音频落盘，返回路径（同步，内部自建事件循环）。

    调用方在后台线程里跑（server 的 stage 线程），所以不会有"事件循环已运行"的冲突；
    真遇到了也要让它抛出来，不要静默吞掉。
    """
    parent = os.path.dirname(out_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    if current_provider() == "minimax":
        return _synth_minimax(text, voice_id, out_path)
    return _synth_edge(text, voice_id, out_path)


def synth_sample(voice_id: str, out_path: str, text: str = "") -> str:
    """用示范文本合成音色样本。"""
    return synth(text or SAMPLE_TEXT, voice_id or default_voice(), out_path)


def list_voices(pool=None) -> list[dict[str, str]]:
    """当前后端的音色清单（给前端下拉用）。单一来源在这里，前端不要自己硬编码一份。"""
    return [
        {
            "id": v["id"], "cn": v["cn"], "gender": v["gender"],
            "locale": v.get("locale", "普通话"), "hint": v["hint"],
        }
        for v in _resolve_pool(pool)
    ]


def provider_label() -> str:
    """当前后端的中文名（界面/日志显示用）。"""
    return {"edge": "edge-tts（免费）", "minimax": "MiniMax"}.get(current_provider(), current_provider())
