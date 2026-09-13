"""批量图生视频入口：把一个案例的所有分镜转成视频。

用法：
  python video_agent.py outputs/42b6e27feab3            # 整个案例，缺视频的都生成
  python video_agent.py outputs/42b6e27feab3 --force    # 已有视频也强制重生成
  python video_agent.py outputs/42b6e27feab3 --dry-run  # 只打印每个分镜的参数，不提交

前置条件（一次性）：
  1. ModelScope 实例已启动，里面跑过 start_comfyui.sh
  2. .env 里 COMFYUI_URL 填了实例 tunnel_url.txt 中的隧道地址

行为细节：
  - 视频输出到 <案例目录>/videos/shot_XX.mp4，已存在且未加 --force 的自动跳过（断点续跑）
  - 旧案例 shots.json 里没有 video_prompt 时，退化为用 scene_desc（中文，Qwen3-VL 能懂）
  - 结束后会把 video_path 写回 shots.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from dotenv import load_dotenv

load_dotenv()

from schemas import Shot  # noqa: E402
from video_provider import ComfyUIVideoProvider  # noqa: E402


def _resolve_image(case_dir: str, shot: Shot) -> str | None:
    """优先用 shots.json 里记录的绝对路径，不在了再退回 case_dir/images/。"""
    if shot.image_path and os.path.exists(shot.image_path):
        return shot.image_path
    guess = os.path.join(case_dir, "images", f"shot_{shot.shot_id:02d}.png")
    return guess if os.path.exists(guess) else None


def main() -> int:
    parser = argparse.ArgumentParser(description="批量图生视频（ComfyUI + MiniMax H3）")
    parser.add_argument("case_dir", help="案例目录（含 shots.json 和 images/）")
    parser.add_argument("--force", action="store_true", help="已存在的视频也重新生成")
    parser.add_argument("--dry-run", action="store_true", help="只打印参数不提交")
    parser.add_argument(
        "--url", default="", help="ComfyUI 隧道地址；不填则用前端面板保存的地址或 COMFYUI_URL"
    )
    args = parser.parse_args()

    case_dir = args.case_dir
    shots_path = os.path.join(case_dir, "shots.json")
    with open(shots_path, encoding="utf-8") as fh:
        raw = json.load(fh)
    shots = [Shot.from_dict(d, i + 1) for i, d in enumerate(raw)]
    # 保留 shots.json 里已有但 dataclass 未覆盖的字段（如 image_path / video_path）
    for d, s in zip(raw, shots):
        s.image_path = str(d.get("image_path", ""))
        s.video_prompt = str(d.get("video_prompt", ""))

    provider = None
    if not args.dry_run:
        provider = ComfyUIVideoProvider(base_url=args.url or None)

    out_dir = os.path.join(case_dir, "videos")
    os.makedirs(out_dir, exist_ok=True)

    todo = []
    for s in shots:
        out_path = os.path.join(out_dir, f"shot_{s.shot_id:02d}.mp4")
        if os.path.exists(out_path) and not args.force:
            print(f"  #{s.shot_id:02d} 跳过（已存在 {out_path}）")
            continue
        img = _resolve_image(case_dir, s)
        if not img:
            print(f"  #{s.shot_id:02d} 跳过（找不到首帧图）")
            continue
        todo.append((s, img, out_path))

    print(f"待生成 {len(todo)} / 共 {len(shots)} 镜")
    done = 0
    for s, img, out_path in todo:
        prompt = s.video_prompt or f"[无 video_prompt，退化为 scene_desc] {s.scene_desc}"
        frames = max(5, round(s.duration * 24))
        frames += (5 - frames % 17) % 17
        if args.dry_run:
            print(f"  #{s.shot_id:02d} [dry-run] {img} -> {out_path}")
            print(f"          length={frames} duration={s.duration}s prompt={prompt[:80]}...")
            done += 1
            continue
        print(f"  #{s.shot_id:02d} 生成中：{img} -> {out_path}")
        try:
            provider.generate(img, prompt, s.duration, out_path)
            s.video_path = out_path
            d = raw[s.shot_id - 1]
            d["video_path"] = out_path
            done += 1
            print(f"  #{s.shot_id:02d} 完成")
        except Exception as exc:  # 单镜失败不拖垮整批，最后统一报
            print(f"  #{s.shot_id:02d} 失败：{exc}")

    with open(shots_path, "w", encoding="utf-8") as fh:
        json.dump(raw, fh, ensure_ascii=False, indent=2)

    failed = len(todo) - done
    print(f"完成 {done}/{len(todo)}" + (f"，失败 {failed}" if failed else ""))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
