"""CLI 入口。

用法：
    python main.py "一个雨夜便利店重逢的故事"        # 不带 --shots，AI 自定镜数
    python main.py "赛博朋克城市的清晨" --shots 8 --ratio 9:16 --concurrency 2
"""

from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

load_dotenv()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="多 Agent 分镜生成器（当前阶段：分镜脚本 + 分镜图片）"
    )
    parser.add_argument("idea", help="一句创意或一段剧情描述")
    parser.add_argument(
        "--shots", type=int, default=None, help="分镜数量；不传则由 AI 按故事节奏自行决定"
    )
    parser.add_argument(
        "--ratio",
        choices=["16:9", "9:16", "1:1", "4:3", "3:4"],
        help="画面比例，默认由导演 Agent 决定",
    )
    parser.add_argument("--out", default="outputs", help="输出目录，默认 outputs")
    parser.add_argument(
        "--concurrency", type=int, default=2, help="出图并发数，默认 2（额度有限，不建议调高）"
    )
    parser.add_argument(
        "--text-only",
        action="store_true",
        help="只生成分镜脚本与提示词，跳过出图（不需要图像模型的密钥）",
    )
    args = parser.parse_args()

    # 延迟导入：pipeline 读环境变量，必须在 load_dotenv 之后加载
    from pipeline import run_pipeline

    run_pipeline(
        user_input=args.idea,
        shot_count=args.shots,
        out_dir=args.out,
        concurrency=args.concurrency,
        ratio=args.ratio,
        text_only=args.text_only,
    )
    print(f"\n产出目录 {args.out}/ ：project.json · shots.json · preview.html · images/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
