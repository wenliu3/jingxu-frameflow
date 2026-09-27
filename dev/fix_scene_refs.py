# -*- coding: utf-8 -*-
"""给《糯糯下山》提示词总表的每一镜补上场景引用。

背景：原表的 frames 列有些镜头只有 character:N，没有 scene:N。
斌哥要求：每镜都是一个独立视频生成任务，都必须带场景。

规则：
- 新增/替换 scene:N 项，并把它排在第一位（Ref2VA 下顺序无关；I2V 下第 1 张当首帧）
- 非 scene 项保持原顺序，不丢
- 输出超过 3 项的镜头清单，供人工取舍
  （⚠️ 3 只是**本脚本自己的保守阈值**，不是 Ref2VA 的上限 —— Ref2VA 实际能吃 9 张参考图，
   见 `server/app.py` 的 `REF2VA_MAX_REFS`。这个脚本是一次性工具，阈值就不改了，
   免得动它当初的输出口径。）
"""
import re
import shutil

PATH = r"D:\Pychrom Project\ai_video_multiagent\docs\糯糯下山_AI视频创作提示词总表.md"
BACKUP = PATH + ".bak"

# {集号: {镜号(str): 场景编号}}
SCENE_MAP = {
    1:  {"1": 0, "2": 1, "3": 1, "4": 1, "5": 1, "6": 1},
    2:  {"1": 1, "2": 9, "3": 9, "4": 1, "5": 1, "6": 1},
    3:  {"1": 1, "2": 1, "3": 11, "4": 1, "5": 1, "6": 1},
    4:  {str(k): 3 for k in range(1, 7)},
    5:  {"1": 2, "2": 3, "3": 3, "4": 3, "5": 2, "6": 2},
    6:  {str(k): 2 for k in range(1, 7)},
    7:  {str(k): 0 for k in range(1, 7)},
    8:  {str(k): 4 for k in range(1, 7)},
    9:  {str(k): 5 for k in range(1, 7)},
    10: {"1": 9, "2": 9, "3": 9, "4": 4, "5": 9},
    11: {str(k): 4 for k in range(1, 6)},
    12: {str(k): 1 for k in range(1, 7)},
    13: {str(k): 4 for k in range(1, 7)},
    14: {str(k): 10 for k in range(1, 6)},
    15: {str(k): 6 for k in range(1, 7)},
    16: {str(k): 6 for k in range(1, 7)},
    17: {"1": 6, "2": 6, "3": 9, "4": 1, "5": 9, "6": 6},
    18: {"1a": 12, "1b": 4, "2": 1, "3": 1, "4": 1, "5": 1},
    19: {str(k): 7 for k in range(1, 7)},
    20: {"1": 8, "2": 8, "3": 8, "4": 8, "5": 9},
}

ROW = re.compile(r"^\| ([0-9]+[ab]?) \| (\d+s) \| 16:9 \| (.+?) \| (.+) \|$")
EP = re.compile(r"^### 第 (\d+) 集")

shutil.copyfile(PATH, BACKUP)
src = open(PATH, encoding="utf-8").read()

out_lines = []
cur_ep = None
changed = 0
overflow = []      # 补完后超过 3 项的镜头（3 是本脚本的保守阈值，不是 Ref2VA 上限）

for line in src.split("\n"):
    m_ep = EP.match(line)
    if m_ep:
        cur_ep = int(m_ep.group(1))
        out_lines.append(line)
        continue

    m = ROW.match(line)
    if not m or cur_ep is None:
        out_lines.append(line)
        continue

    shot, dur, frames_cell, rest = m.group(1), m.group(2), m.group(3), m.group(4)
    refs = re.findall(r"`([a-z]+:\d+)`", frames_cell)
    sc = SCENE_MAP.get(cur_ep, {}).get(shot)

    if sc is None:
        out_lines.append(line)
        continue

    others = [r for r in refs if not r.startswith("scene:")]
    new_refs = [f"scene:{sc}"] + others
    new_cell = " + ".join(f"`{r}`" for r in new_refs)
    new_line = f"| {shot} | {dur} | 16:9 | {new_cell} | {rest} |"

    if new_line != line:
        changed += 1
    if len(new_refs) > 3:
        overflow.append((cur_ep, shot, new_refs))
    out_lines.append(new_line)

open(PATH, "w", encoding="utf-8").write("\n".join(out_lines))
print(f"已备份到 {BACKUP}")
print(f"改写了 {changed} 行")
print(f"超过 3 项（本脚本的保守阈值；Ref2VA 真上限是 9）的镜头：{len(overflow)} 个")
for ep, shot, refs in overflow:
    print(f"  第 {ep} 集 镜 {shot}: {refs}")
