# -*- coding: utf-8 -*-
"""删除每集末尾已过时的「首帧来源」段落。

原因：那份建议是按 i2v（单图首帧）口径写的，说"某镜需出分镜图"。
切到 Ref2VA 后素材图本身就是参考输入，不再需要额外出首帧图，这些段落会误导。
"""
PATH = r"D:\Pychrom Project\ai_video_multiagent\docs\糯糯下山_AI视频创作提示词总表.md"

lines = open(PATH, encoding="utf-8").read().split("\n")
kept, removed = [], 0
for line in lines:
    if line.startswith("**首帧来源**："):
        removed += 1
        continue
    kept.append(line)

# 折叠因删除产生的连续空行（最多保留一个）
out = []
for line in kept:
    if line.strip() == "" and out and out[-1].strip() == "":
        continue
    out.append(line)

open(PATH, "w", encoding="utf-8").write("\n".join(out))
print(f"删除了 {removed} 行「首帧来源」段落")
