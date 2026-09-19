# -*- coding: utf-8 -*-
"""「AI 生成角色图」提示词链路验证 —— 默认**不烧额度**（零外部调用）。

跑法（在项目根）：
    D:/miniforge/python.exe _verify_portrait_prompt.py

验的是 2026-09-19 那次改动的**落地效果**（改的是提示词文本 / 负向词 / 设定图版式与画幅），
**不验出图效果**（那要烧图像日额度）：

  1) 单张定妆照的提示词里**不许再出现**「四张图」「多视角设定图」这类数量词。
     旧版正是这两句让模型把单张画成四格拼图 —— 实测 `金鳞.png` / `莫卡.png` 变成
     1024×576 四格、`紊流.png` 变成 2×2，而它们本该是 1:1 单人半身。
     并且必须写明「整幅画面只有这一个人物」「不分格」。
  2) 单张的 negative 必须是 `PORTRAIT_NEGATIVE`（含 grid / collage / 分格 / 拼贴）。
  3) 设定图：尺寸必须是 **16:9**（1024x576，四格横排才排得下）；提示词必须是
     `PORTRAIT_SHEET_LAYOUT + 人物描述段 + PORTRAIT_SHEET_TAIL` —— **版式段必须在最前**。
     2026-09-19 实测教训：人物段（600+ 字）压在版式段前面时，模型几乎不看构图指令，
     莫卡那张设定图连续三次丢掉左格的面部特写、退化成"三张全身像"的转身图。
     LAYOUT 里必须写明「16:9 横版角色设定图」「巨大的面部半身特写」「恰好这四个视角」
     「绝对不要画成全身」。
  4) 设定图的 negative 必须是 `SHEET_NEGATIVE`（禁竖排两栏 / 格数不对），且**不含「分格」**
     —— 它本来就是分格图，禁分格等于自相矛盾。
  5) 落库：`image_path` → `<名>.png`，`sheet` → `<名>_sheet.png`，`images` **只有单人图**。
  6) 第二次出图时快照真的存下了旧版（历史目录里 1 版 / 2 个文件）。

做法：进程内调 `server_app.generate_character_portrait`，把 `create_provider` 换成假 provider
（记下 prompt / negative / size / seed 后写一张真 PNG 就返回），把 `agents.chat_json` 换成固定
返回（角色 Agent 那次文本调用也不真发）。跑完通过 HTTP 把测试作品 DELETE 掉。
"""
import json
import os
import struct
import sys
import urllib.error
import urllib.request
import zlib

ROOT = os.path.dirname(os.path.abspath(__file__))
API = os.environ.get("E2E_API", "http://127.0.0.1:8000").rstrip("/")

PERSON = "【假的人物描述段：银灰短发，黑色高领，左眉有一道浅旧疤】"
CHAR_NAME = "验证角色"

results: list[bool] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    results.append(bool(cond))
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"\n        → {extra}" if extra else ""))


def http(method: str, path: str, payload=None):
    data, headers = None, {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(API + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw or "null")
        except Exception:
            return e.code, {"detail": raw}


def tiny_png(w: int = 8, h: int = 8, rgb=(200, 200, 200)) -> bytes:
    """手写一张纯色 PNG（不依赖 Pillow）—— 只为让产物是真图、非 0 字节。"""
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


CALLS: list[dict] = []


class FakeProvider:
    """假图像 provider：签名与 `ImageProvider.generate` 对齐，只记录参数 + 写真 PNG。

    ⚠️ 签名少一个参数就会 TypeError，而表现是"出图失败"而不是"测试脚本错了"。
    真实 provider 加参数时记得同步改这里。
    """

    model = "fake-image-model"

    def generate(self, prompt, out_path, negative_prompt="", size="1024x576", seed=None):
        CALLS.append({
            "prompt": prompt, "negative": negative_prompt, "size": size, "seed": seed,
            "path": out_path,
        })
        parent = os.path.dirname(out_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(tiny_png())
        return out_path


task_id = ""
try:
    print("=" * 74)
    print("「AI 生成角色图」提示词链路验证（不烧额度）   " + API)
    print("=" * 74 + "\n")

    # ---------- 0) 建测试作品 + 一个角色（design=False，不调模型） ----------
    st, draft = http("POST", "/api/tasks/draft", {"title": "定妆照提示词验证"})
    task_id = (draft or {}).get("task_id", "")
    check("建出测试作品并拿到 task_id", bool(task_id), task_id)

    st, ch = http("POST", f"/api/tasks/{task_id}/characters",
                  {"name": CHAR_NAME, "design": False})
    check("角色落库（design=False，不碰模型）",
          st == 200 and (ch or {}).get("name") == CHAR_NAME,
          f"HTTP {st} name={(ch or {}).get('name')}")

    # ---------- 1) 进程内跑一次出图（假 provider + 假文本调用，零外部调用） ----------
    sys.path.insert(0, os.path.join(ROOT, "server"))
    import app as server_app          # noqa: E402
    import agents                     # noqa: E402
    import pipeline                   # noqa: E402

    server_app.create_provider = lambda: FakeProvider()
    agents.chat_json = lambda system, user, **kw: {"prompt": PERSON}

    updated = server_app.generate_character_portrait(
        task_id, 0,
        server_app.CharacterPortraitBody(prompt="银灰短发的女战士，黑色高领", ratio="1:1"),
    )
    check("两次出图都发生了（单张 + 设定图）", len(CALLS) == 2, f"{len(CALLS)} 次")
    if len(CALLS) != 2:
        raise SystemExit("出图次数不对，后面的断言没意义")

    single, sheet = CALLS[0], CALLS[1]

    # ---------- 2) 单张定妆照的提示词 ----------
    print("\n  —— 单张定妆照 ——")
    sp = single["prompt"]
    check("单张提示词里没有「四张图」", "四张图" not in sp, sp[:60] + "…")
    check("单张提示词里没有「多视角设定图」", "多视角设定图" not in sp)
    check("单张提示词里没有「与其他视角」", "与其他视角" not in sp)
    check("写明了「整幅画面只有这一个人物」", "整幅画面只有这一个人物" in sp)
    check("写明了「只呈现这一个视角」", "只呈现这一个视角" in sp)
    check("写明了「不分格、不拼贴」", "不分格、不拼贴" in sp)
    check("人物描述段带进去了", PERSON in sp, PERSON[:24] + "…")
    check("画幅 = 1:1（1024x1024）", single["size"] == "1024x1024", single["size"])
    check("negative 是 PORTRAIT_NEGATIVE（不是全局 DEFAULT_NEGATIVE）",
          single["negative"] == pipeline.PORTRAIT_NEGATIVE)
    for kw in ("grid", "collage", "分格", "拼贴", "多视角"):
        check(f"negative 含「{kw}」", kw in single["negative"])

    # ---------- 3) 设定图的提示词 ----------
    print("\n  —— 设定图 ——")
    shp = sheet["prompt"]
    check("设定图提示词 = LAYOUT + 人物段 + TAIL",
          shp == f"{agents.PORTRAIT_SHEET_LAYOUT}\n人物特征：{PERSON}\n{agents.PORTRAIT_SHEET_TAIL}")
    # ⚠️ 版式段必须在最前：人物段 600+ 字压在它前面时，模型几乎不看后面的构图指令
    #    （莫卡那张连续三次丢掉左格特写就是这个原因）
    check("版式段在最前面（人物段不许压在它前面）",
          shp.index(agents.PORTRAIT_SHEET_LAYOUT) < shp.index(PERSON))
    check("LAYOUT 写了「16:9 横版角色设定图」", "16:9 横版角色设定图" in shp)
    check("LAYOUT 写了「巨大的面部半身特写」", "巨大的面部半身特写" in shp)
    check("LAYOUT 写了「恰好这四个视角」", "恰好这四个视角" in shp)
    check("LAYOUT 写了「绝对不要画成全身」（左格必须是上半身）", "绝对不要画成全身" in shp)
    check("LAYOUT 写了「不允许两格重复同一个视角」", "不允许两格重复同一个视角" in shp)
    check("TAIL 写了「不要竖排两栏」", "不要竖排两栏" in shp)
    check("TAIL 写了「不要三张全身像并排的转身图」", "不要三张全身像并排的转身图" in shp)
    check("TAIL 写了「16:9 横版构图」", "16:9 横版构图" in shp)
    check("画幅 = 16:9（1024x576，横版四格）", sheet["size"] == "1024x576", sheet["size"])
    check("negative 是 SHEET_NEGATIVE", sheet["negative"] == pipeline.SHEET_NEGATIVE)
    check("设定图的 negative 含「竖排两栏」", "竖排两栏" in sheet["negative"])
    check("设定图的 negative 含「转身图」（防退化成 turnarouond 三全身）",
          "转身图" in sheet["negative"])
    check("设定图的 negative **不含**「分格」（它本来就是分格图）",
          "分格" not in sheet["negative"])

    # ---------- 4) 落库与产物 ----------
    print("\n  —— 落库与产物 ——")
    cdir = os.path.join(ROOT, "outputs", task_id, "characters")
    front = os.path.join(cdir, f"{CHAR_NAME}.png")
    sheetp = os.path.join(cdir, f"{CHAR_NAME}_sheet.png")
    check("正面单张文件真的写出来了", os.path.isfile(front), front)
    check("设定图文件真的写出来了", os.path.isfile(sheetp), sheetp)
    check("返回值 image_path 指向单张", os.path.basename(updated.get("image_path") or "") == f"{CHAR_NAME}.png",
          os.path.basename(updated.get("image_path") or ""))
    check("返回值 sheet 指向设定图", os.path.basename(updated.get("sheet") or "") == f"{CHAR_NAME}_sheet.png",
          os.path.basename(updated.get("sheet") or ""))
    check("images 只有单人图（设定图不进 images）",
          [os.path.basename(p) for p in (updated.get("images") or [])] == [f"{CHAR_NAME}.png"],
          str([os.path.basename(p) for p in (updated.get("images") or [])]))

    proj_path = os.path.join(ROOT, "outputs", task_id, "project.json")
    with open(proj_path, encoding="utf-8") as fh:
        on_disk = json.load(fh)
    dc = (on_disk.get("characters") or [{}])[0]
    check("磁盘 project.json 的 images 也只有单人图",
          [os.path.basename(p) for p in (dc.get("images") or [])] == [f"{CHAR_NAME}.png"])
    check("磁盘 project.json 的 sheet 指向设定图",
          os.path.basename(dc.get("sheet") or "") == f"{CHAR_NAME}_sheet.png")
    check("version 换新了（cache-busting 用）", bool(updated.get("version")), updated.get("version"))

    # ---------- 5) 再跑一次 → 快照该存下旧版 ----------
    print("\n  —— 第二次出图（验快照） ——")
    CALLS.clear()
    server_app.generate_character_portrait(
        task_id, 0, server_app.CharacterPortraitBody(prompt="换成短发版", ratio="1:1"))
    hist = os.path.join(ROOT, "outputs", task_id, "history", "character", CHAR_NAME)
    vers = sorted(os.listdir(hist)) if os.path.isdir(hist) else []
    check("历史目录建出来了", os.path.isdir(hist), hist)
    check("存下了 1 版", len(vers) == 1, str(vers))
    if vers:
        snap = sorted(os.listdir(os.path.join(hist, vers[0])))
        check("那一版里存了 2 个文件（单张 + 设定图）", len(snap) == 2, str(snap))

    print("\n  —— 收尾 ——")
    st, _ = http("DELETE", f"/api/tasks/{task_id}?confirm=true")
    check("测试作品已删掉", st == 200, f"HTTP {st}")
    check("outputs/ 下没留目录", not os.path.isdir(os.path.join(ROOT, "outputs", task_id)))

finally:
    # 中途抛异常时也要把测试作品清掉（正常路径上面已经删过，这里再删一次是 404，无害）。
    if task_id and os.path.isdir(os.path.join(ROOT, "outputs", task_id)):
        try:
            http("DELETE", f"/api/tasks/{task_id}?confirm=true")
        except Exception:
            pass

# ⚠️ 汇总与 sys.exit 必须在 finally 外面：写进 finally 里时中途抛异常会被它顶掉，
#    脚本照样打印「n/n 通过」并以 0 退出（_verify_trash.py 真发生过）。
passed = sum(1 for r in results if r)
print()
print(f"{passed}/{len(results)}")
sys.exit(0 if passed == len(results) else 1)
