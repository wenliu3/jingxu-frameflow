"""配置自检：一次性定位密钥 / 网络 / 模型名的问题。

用法：
    python doctor.py            # 查配置 + DeepSeek 连通性
    python doctor.py --image    # 额外真实出图一次（会消耗额度）

设计原则：只显示密钥的前后几位，不完整打印，避免泄露。
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
load_dotenv(os.path.join(HERE, ".env"))

import requests  # noqa: E402

PASS = "[ OK ]"
FAIL = "[FAIL]"
INFO = "[INFO]"


def mask(value: str) -> str:
    if not value:
        return "(空)"
    if len(value) <= 10:
        return f"{value[:2]}...({len(value)} 字符)"
    return f"{value[:7]}...{value[-4:]} ({len(value)} 字符)"


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> None:
    want_image = "--image" in sys.argv

    section("1. 配置文件")
    env_path = os.path.join(HERE, ".env")
    if os.path.exists(env_path):
        print(f"{PASS} .env 存在：{env_path}")
    else:
        print(f"{FAIL} 没有 .env 文件。先执行：copy .env.example .env")
        return

    section("2. 环境变量")
    keys = [
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_MODEL",
        "MODELSCOPE_API_TOKEN",
        "MODELSCOPE_IMAGE_MODEL",
        "IMAGE_PROVIDER",
        "AGNES_API_KEY",
    ]
    values = {}
    for k in keys:
        v = (os.getenv(k) or "").strip()
        values[k] = v
        if not v:
            print(f"{INFO} {k:<24} 未配置")
        else:
            mark = PASS
            # 常见错误：值里带了引号或空格
            if v != v.strip() or v.startswith(("'", '"')):
                mark = FAIL
                print(f"{mark} {k:<24} {mask(v)}  <- 值里可能带了引号或空格")
            else:
                print(f"{mark} {k:<24} {mask(v)}")

    section("3. DeepSeek 连通性")
    key = values.get("DEEPSEEK_API_KEY", "")
    model = values.get("DEEPSEEK_MODEL") or "deepseek-flash"
    if not key:
        print(f"{FAIL} 跳过：DEEPSEEK_API_KEY 未配置")
    else:
        url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
        body = {
            "model": model,
            "messages": [{"role": "user", "content": "回复两个字：正常"}],
            "max_tokens": 32,
            "thinking": {"type": "disabled"},
        }
        try:
            r = requests.request(
                "post",
                f"{url}/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=body,
                timeout=60,
            )
            if r.status_code == 200:
                content = (r.json()["choices"][0]["message"].get("content") or "").strip()
                print(f"{PASS} 调用成功，模型 {model} 返回：{content!r}")
            else:
                print(f"{FAIL} HTTP {r.status_code}：{r.text[:400]}")
                if r.status_code == 400 and "model" in r.text.lower():
                    print(f"{INFO} 提示：模型名可能不对。当前用的是 {model}，现役应为 deepseek-flash")
                if r.status_code == 401:
                    print(f"{INFO} 提示：密钥无效或已过期")
                if r.status_code == 402:
                    print(f"{INFO} 提示：账户余额不足")
        except Exception as exc:
            print(f"{FAIL} 请求异常：{type(exc).__name__}: {exc}")

    section("4. 图像通道")
    provider_name = (os.getenv("IMAGE_PROVIDER") or "modelscope").strip().lower()
    print(f"{INFO} IMAGE_PROVIDER = {provider_name}")
    if provider_name == "modelscope":
        if not values.get("MODELSCOPE_API_TOKEN"):
            print(f"{FAIL} MODELSCOPE_API_TOKEN 未配置")
        else:
            tok = values["MODELSCOPE_API_TOKEN"]
            if not tok.startswith("ms-"):
                print(f"{INFO} Token 未以 ms- 开头，请确认复制完整（图像接口通常保留该前缀）")
            print(f"{INFO} 模型：{values.get('MODELSCOPE_IMAGE_MODEL') or 'Tongyi-MAI/Z-Image-Turbo'}")
            print(f"{INFO} 常见失败原因：未绑定阿里云账号 / 未实名认证 / 单模型日额度已用尽")
    else:
        if not values.get("AGNES_API_KEY"):
            print(f"{FAIL} AGNES_API_KEY 未配置")

    if not want_image:
        print(f"\n{INFO} 加 --image 参数可真实出图一次，验证整条图像链路")
        return

    section("5. 真实出图测试")
    try:
        from image_provider import create_provider

        provider = create_provider()
        out = os.path.join(HERE, "outputs", "_doctor_test.png")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        print(f"{INFO} 使用 {type(provider).__name__} / {provider.model}")
        provider.generate(
            "a red apple on a wooden table, soft daylight",
            out,
            size="1024x576",
        )
        size = os.path.getsize(out) if os.path.exists(out) else 0
        if size > 1024:
            print(f"{PASS} 出图成功：{out}（{size} bytes）")
        else:
            print(f"{FAIL} 文件异常，仅 {size} bytes")
    except Exception as exc:
        print(f"{FAIL} 出图失败：{type(exc).__name__}: {str(exc)[:500]}")


if __name__ == "__main__":
    main()
