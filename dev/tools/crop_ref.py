"""多视角角色拼贴图裁切工具。

把豆包 / 即梦这类模型产出的「多视角角色设定图」（一张图里并排几个视角）
自动切成单视角图，供定妆照或图生图参考图使用。

原理：拼贴是纯白底，人物之间的分隔处是贯穿上下的白底列——扫描每列的
最小亮度（人物区每列必有深色像素，白缝列整列都白），找出白缝即分隔。

用法：
    python dev/tools/crop_ref.py 拼贴图.png --names front,full,side,back
    python dev/tools/crop_ref.py 拼贴图.png --split 682,1137,1592   # 检测失败时手动给分隔点

产出与输入同目录，命名 <输入名>_01.png ... 或 --names 给出的语义名。
"""

from __future__ import annotations

import argparse
import os

from PIL import Image


def find_segments(im: Image.Image, dark_thresh: int = 180, min_dark: int | None = None,
                  min_gap: int = 6, min_width: int = 60) -> list[tuple[int, int]]:
    """按列暗像素计数找出人物区块。返回 [(x0, x1), ...]，从左到右。

    用暗像素计数而不是列最小亮度：白缝里常带浅色投影（亮度 200-240），
    min 会误判成人物；但真人物列必有深色头发/衣物（<180），阴影没有。

    ⚠️ min_dark 默认**自适应**（留 None）。写死 2 只在"纯白底拼贴"上成立；
    背景是浅灰、人物间隙窄的设定图里，缝隙列常飘着几像素头发/阴影，
    恒定阈值会把四个视角并成一块（2026-09-15 用真实 3D 国漫设定图实测：只切出 1 块）。
    自适应取"最密集人物列的 10%"为界：人物列必然远超此值，缝隙列必然低于此值。
    """
    g = im.convert("L")
    W, H = im.size
    small = g.resize((W, max(1, H // 4)), Image.BOX)
    px = small.load()
    darks = [sum(1 for y in range(small.height) if px[x, y] < dark_thresh)
             for x in range(W)]
    if min_dark is None:
        hi = max(darks) if darks else 0
        min_dark = max(2, int(hi * 0.10))

    segs: list[tuple[int, int]] = []
    start: int | None = None
    gap = 0
    for x, d in enumerate(darks):
        if d >= min_dark:                   # 这列有人物像素
            if start is None:
                start = x
            gap = 0
        else:                               # 白缝列
            if start is not None:
                gap += 1
                if gap >= min_gap:          # 连续白缝 = 区块结束
                    if x - gap - start + 1 >= min_width:
                        segs.append((start, x - gap + 1))
                    start = None
                    gap = 0
    if start is not None and W - start >= min_width:
        segs.append((start, W))
    return segs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--names", default="", help="逗号分隔的语义名，如 front,full,side,back")
    ap.add_argument("--split", default="", help="手动分隔点（像素，逗号分隔），跳过自动检测")
    ap.add_argument("--out", default="", help="输出目录，默认与输入同目录")
    args = ap.parse_args()

    im = Image.open(args.image)
    W, H = im.size
    if args.split:
        cuts = [int(v) for v in args.split.split(",")]
        segs = list(zip([0] + cuts, cuts + [W]))
    else:
        segs = find_segments(im)
        if not 2 <= len(segs) <= 8:
            raise SystemExit(
                f"自动检测到 {len(segs)} 个区块（{segs}），不像正常拼贴。"
                "请用 --split 手动指定分隔点。"
            )
        print(f"检测到 {len(segs)} 个区块：{segs}")

    names = [n for n in args.names.split(",") if n]
    out_dir = args.out or os.path.dirname(os.path.abspath(args.image))
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(args.image))[0]
    for i, (x0, x1) in enumerate(segs):
        name = names[i] if i < len(names) else f"{stem}_{i + 1:02d}"
        path = os.path.join(out_dir, f"{name}.png")
        im.crop((x0, 0, x1, H)).save(path)
        print(f"  {path}  ({x1 - x0}x{H})")


if __name__ == "__main__":
    main()
