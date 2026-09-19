"""拼贴直发实验：验证「整张多视角拼贴 + Edit 模型 -> 同排版新角色拼贴」路线。

背景：斌哥拿豆包的多视角设定图提出「直接把拼贴发给生图模型，让它生成类似的」。
如果可行，一次调用出四个视角且天然同一个人，全面优于逐视角 Edit（3 次调用）。
本脚本一次调用给答案，不动任何产品代码。

用法：
    python test_collage_edit.py                     # 用默认测试角色
    python test_collage_edit.py --anchor "新的角色描述"
    python test_collage_edit.py --image 某拼贴.png --out 输出.png

产物默认写 outputs/_collage_test.png。看三点：排版有没有跟住、
四张是不是同一个人、有没有残留参考图人物的特征。
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv

load_dotenv()

from image_provider import ModelScopeProvider

DEFAULT_ANCHOR = (
    "40岁东亚女性，齐耳银灰色短发，左脸颊有一颗黑痣，"
    "穿米白色高领毛衣和深棕色背带长裤，圆框眼镜"
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default=os.path.join("character_refs", "ref_sheet_raw.png"))
    ap.add_argument("--anchor", default=DEFAULT_ANCHOR)
    ap.add_argument("--out", default=os.path.join("outputs", "_collage_test.png"))
    ap.add_argument(
        "--keep", action="store_true",
        help="保持参考图人物不变（产品真实场景：拿本角色正面图扩多视角）",
    )
    args = ap.parse_args()

    if not os.path.isfile(args.image):
        raise SystemExit(f"拼贴图不存在：{args.image}")

    if args.keep:
        prompt = (
            "参考这张角色多视角设定图的排版与光线：左侧为正面半身特写，"
            "右侧竖排三张完整全身像，从上到下依次为正面全身（头顶到脚完整入画）、"
            "侧面全身（正侧面90度）、背面全身。纯浅色背景，均匀柔光无阴影。"
            "保持参考图左侧这位人物的外貌、发型、服装完全不变，"
            f"（人物特征：{args.anchor}），画出同一人的四视角设定图。"
            "四个视角必须是同一个人、同一套服装。"
        )
    else:
        prompt = (
            "参考这张角色多视角设定图的排版与光线：左侧为正面半身特写，"
            "右侧竖排三张完整全身像，从上到下依次为正面全身（头顶到脚完整入画）、"
            "侧面全身（正侧面90度）、背面全身。纯浅色背景，均匀柔光无阴影。"
            f"按此排版绘制一个全新的角色：{args.anchor}。"
            "四个视角必须是同一个人、同一套服装，且与参考图中的人物完全不同。"
        )

    provider = ModelScopeProvider()
    print(f"拼贴直发实验 | 模式：{'保持原人物' if args.keep else '替换为新角色'} | Edit 模型：{provider.edit_model}")
    print(f"参考图：{args.image}")
    print(f"角色：{args.anchor[:60]}…")
    provider.generate(prompt, args.out, ref_image=args.image)
    print(f"完成 -> {args.out}")
    print("核对三点：①排版是否跟住  ②四个视角是否同一人  ③是否残留参考图人物特征")


if __name__ == "__main__":
    main()
