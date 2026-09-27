# -*- coding: utf-8 -*-
"""校验：每镜 frames 第一项是场景、总数不超过 3、无残留旧口径。"""
import re

PATH = r"D:\Pychrom Project\ai_video_multiagent\docs\糯糯下山_AI视频创作提示词总表.md"
ROW = re.compile(r"^\| ([0-9]+[ab]?) \| (\d+s) \| 16:9 \| (.+?) \| (.+) \|$")
EP = re.compile(r"^### 第 (\d+) 集")

rows = 0
no_scene = []
too_many = []
cur_ep = None
bad_scene_use = []
used_scenes = set()

for line in open(PATH, encoding="utf-8"):
    line = line.rstrip("\n")
    m_ep = EP.match(line)
    if m_ep:
        cur_ep = int(m_ep.group(1))
        continue
    m = ROW.match(line)
    if not m or cur_ep is None:
        continue
    rows += 1
    shot, refs = m.group(1), re.findall(r"`([a-z]+:\d+)`", m.group(3))
    if not refs or not refs[0].startswith("scene:"):
        no_scene.append((cur_ep, shot, refs))
    if len(refs) > 3:
        too_many.append((cur_ep, shot, refs))
    for r in refs:
        if r.startswith("scene:"):
            used_scenes.add(int(r.split(":")[1]))

print(f"镜头行总数: {rows}")
print(f"首项不是场景的: {len(no_scene)}")
for ep, shot, refs in no_scene:
    print(f"   第 {ep} 集 镜 {shot}: {refs}")
print(f"超过 3 张的: {len(too_many)}")
for ep, shot, refs in too_many:
    print(f"   第 {ep} 集 镜 {shot}: {refs}")
print(f"实际用到的场景编号: {sorted(used_scenes)}")
print(f"定义了但没被任何镜头引用的场景: {sorted(set(range(13)) - used_scenes)}")
