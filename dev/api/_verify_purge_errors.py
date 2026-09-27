# -*- coding: utf-8 -*-
"""验证「彻底删除」失败时**能给出可读的错误**（2026-09-19 加）。

背景：原来 `purge_trash` 里是裸的 `shutil.rmtree(src)`。WorkBuddy 的沙箱安全删除 shim
在批量护栏触发时是 `raise SystemExit(1)`（**不是 OSError**），冒到 ASGI 就被 Starlette
变成一句 `text/plain` 的 "Internal Server Error"，前端连原因都拿不到 ——
再叠加 `api.js` 的 body 读两次，用户看到的就是
「Failed to execute 'text' on 'Response': body stream already read」。

这条脚本**不烧额度、不碰任何真作品**：造一个假回收站条目，monkeypatch 掉 rmtree
分别模拟三种失败，断言都能变成带 detail 的 HTTPException。

跑法（在项目根）：
    D:/miniforge/python.exe dev/api/_verify_purge_errors.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "server"))

import app as server_app                                    # noqa: E402

results: list[bool] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    results.append(bool(cond))
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"\n        → {extra}" if extra else ""))


# 假条目：形状必须合法（%Y%m%d_%H%M%S_12位id），否则 _resolve_trash 会先 400
FAKE = "20260101_000000_ffffffffffff"
fake_dir = os.path.join(ROOT, "outputs", "_trash", FAKE)


def purge():
    return server_app.purge_trash(FAKE, confirm=True)


def expect_500(needle: str) -> tuple[bool, str]:
    """调 purge，期望拿到 500 的 HTTPException 且 detail 里有 needle。"""
    try:
        purge()
    except server_app.HTTPException as exc:
        return (exc.status_code == 500 and needle in str(exc.detail)), f"HTTP {exc.status_code} detail={exc.detail!r}"
    except BaseException as exc:                            # noqa: BLE001
        return False, f"冒出了 {type(exc).__name__}: {exc}"
    return False, "居然成功了（没抛异常）"


print("=" * 74)
print("彻底删除的失败路径 · 后端验证")
print("=" * 74 + "\n")

os.makedirs(fake_dir, exist_ok=True)
with open(os.path.join(fake_dir, "project.json"), "w", encoding="utf-8") as fh:
    fh.write('{"title": "失败路径验证"}')

real_rmtree = server_app.shutil.rmtree

try:
    # ---------- 0) 前置：确认这个假条目能被解析到 ----------
    print("-- 0) 前置 --")
    try:
        src, tid = server_app._resolve_trash(FAKE)
        check("假条目能解析（形状合法）", src == fake_dir and tid == "ffffffffffff", f"{src} / {tid}")
    except server_app.HTTPException as exc:
        check("假条目能解析（形状合法）", False, f"{exc.status_code} {exc.detail}")
        raise SystemExit(1)

    # ---------- 1) 确认闸还在 ----------
    print("\n-- 1) confirm 确认闸 --")
    try:
        server_app.purge_trash(FAKE, confirm=False)
        check("不带 confirm → 409", False, "居然成功了")
    except server_app.HTTPException as exc:
        check("不带 confirm → 409", exc.status_code == 409, f"HTTP {exc.status_code}")

    # ---------- 2) 沙箱护栏：SystemExit ----------
    print("\n-- 2) 沙箱护栏（raise SystemExit）--")
    def boom_systemexit(_path, *a, **kw):
        raise SystemExit(1)
    server_app.shutil.rmtree = boom_systemexit
    ok, extra = expect_500("沙箱")
    check("SystemExit 被接住并变成 500 + 可读 detail", ok, extra)
    check("detail 里有可操作的指引（告诉用户自己启动后端）",
          "uvicorn" in extra or "终端" in extra, extra)

    # ---------- 3) 普通 OSError ----------
    print("\n-- 3) 普通 OSError --")
    def boom_oserror(_path, *a, **kw):
        raise PermissionError("[WinError 32] 文件正被另一个进程使用")
    server_app.shutil.rmtree = boom_oserror
    ok, extra = expect_500("彻底删除失败")
    check("OSError 被接住并带上原始原因", ok, extra)
    check("原始错误信息没被吞掉", "WinError 32" in extra, extra)

    # ---------- 4) 静默失败（没抛异常但目录还在）----------
    print("\n-- 4) 静默失败（护栏在动手前就拦，目录原封不动）--")
    def silent_noop(_path, *a, **kw):
        return None
    server_app.shutil.rmtree = silent_noop
    ok, extra = expect_500("沙箱")
    check("没抛异常但目录还在 → 也要报错，不能假装成功", ok, extra)

finally:
    server_app.shutil.rmtree = real_rmtree
    # 假条目要清干净：它形状合法，会出现在用户的回收站列表里
    try:
        real_rmtree(fake_dir)
    except BaseException as exc:                            # noqa: BLE001
        print(f"\n⚠️ 清理假条目失败，请手动删 {fake_dir}：{exc}")
    else:
        print("\n清理：假条目已删除")

bad = results.count(False)
print("\n" + "=" * 74)
print(f"结果：{len(results) - bad}/{len(results)} 通过" + ("  ← 有 FAIL" if bad else ""))
print("=" * 74)
sys.exit(1 if bad else 0)
