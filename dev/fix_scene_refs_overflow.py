# -*- coding: utf-8 -*-
"""处理补完场景后超过 Ref2VA 3 张上限的镜头。

取舍原则：保留「场景 + 本镜最主要的 2 个角色」，其余角色靠提示词与已建立的形象。
五人同框镜头本来一致性就难保，参考图给主要两位即可。
"""
import re

PATH = r"D:\Pychrom Project\ai_video_multiagent\docs\糯糯下山_AI视频创作提示词总表.md"

OVERRIDE = {
    (7, "5"):  ["scene:0", "character:0", "character:3"],   # 砍 prop:1 仙丹，道具靠提示词
    (10, "2"): ["scene:9", "character:1", "character:2"],   # 五人横移，留前两位
    (16, "5"): ["scene:6", "character:1", "character:2"],
    (18, "1b"): ["scene:4", "character:3", "character:4"],
    (18, "2"): ["scene:1", "character:0", "character:1"],
    (18, "4"): ["scene:1", "character:1", "character:2"],
    (19, "4"): ["scene:7", "character:1", "character:2"],
}

ROW = re.compile(r"^\| ([0-9]+[ab]?) \| (\d+s) \| 16:9 \| (.+?) \| (.+) \|$")
EP = re.compile(r"^### 第 (\d+) 集")

lines = open(PATH, encoding="utf-8").read().split("\n")
out, cur_ep, hit = [], None, 0

for line in lines:
    m_ep = EP.match(line)
    if m_ep:
        cur_ep = int(m_ep.group(1))
        out.append(line)
        continue
    m = ROW.match(line)
    if not m or cur_ep is None:
        out.append(line)
        continue
    shot, dur, _cell, rest = m.group(1), m.group(2), m.group(3), m.group(4)
    new_refs = OVERRIDE.get((cur_ep, shot))
    if new_refs:
        cell = " + ".join(f"`{r}`" for r in new_refs)
        out.append(f"| {shot} | {dur} | 16:9 | {cell} | {rest} |")
        hit += 1
    else:
        out.append(line)

open(PATH, "w", encoding="utf-8").write("\n".join(out))
print(f"处理了 {hit} 个超限镜头")
