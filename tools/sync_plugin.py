#!/usr/bin/env python3
"""把根目录的 gif_digest.py 同步进插件包（plugins/dsh-framedelta/py/gif_digest.py）。

为什么要有这一份副本：插件包必须自包含 —— 装进 profile 的包不该回头去翻工作区的
某个绝对路径。代价是"两份文件会漂移"，所以这份脚本负责同步，`--check` 负责在漂移时
报失败（可以挂进 CI 或提交前手跑）。

用法：
    python tools/sync_plugin.py           # 复制 + 打印哈希
    python tools/sync_plugin.py --check   # 只比对，不一致则退出码 1
"""

from __future__ import annotations

import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "gif_digest.py"
TARGET = ROOT / "plugins" / "dsh-framedelta" / "py" / "gif_digest.py"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def main(argv: list[str]) -> int:
    check_only = "--check" in argv[1:]
    if not SOURCE.exists():
        print(f"源文件不存在：{SOURCE}", file=sys.stderr)
        return 2

    same = TARGET.exists() and SOURCE.read_bytes() == TARGET.read_bytes()
    if check_only:
        if not TARGET.exists():
            print(f"缺少插件副本：{TARGET}\n跑 python tools/sync_plugin.py", file=sys.stderr)
            return 1
        if not same:
            print(f"插件副本与根目录不一致：{TARGET}\n跑 python tools/sync_plugin.py", file=sys.stderr)
            return 1
        print(f"一致：{TARGET.name} {digest(TARGET)}")
        return 0

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    if same:
        print(f"已经一致，无需复制：{TARGET} {digest(TARGET)}")
        return 0
    shutil.copyfile(SOURCE, TARGET)
    if SOURCE.read_bytes() != TARGET.read_bytes():
        print("复制后校验失败", file=sys.stderr)
        return 1
    print(f"已同步：{SOURCE.name} -> {TARGET.relative_to(ROOT)} {digest(TARGET)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
