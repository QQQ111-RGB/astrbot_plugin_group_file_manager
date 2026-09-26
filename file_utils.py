"""文件名 / 数字 ID / 体积 的纯工具函数。

本插件**不保存任何群文件索引**，所以这里只剩与存储无关的纯函数：

* :func:`human_size` —— 字节数转易读字符串
* :func:`numeric_id` —— 由任意字符串派生稳定的 9 位数字短 ID（网盘条目的「文件ID」）
* :func:`normalize_name` —— 文件名归一化，用于容错匹配
* :func:`now_ts` —— 当前时间戳

没有 IO、没有外部依赖，方便单测。
"""

from __future__ import annotations

import hashlib
import time

__all__ = ["human_size", "normalize_name", "now_ts", "numeric_id"]

# 归一化时要剥掉的「噪音字符」：空格 / 制表符 / 全角空格 / 下划线 / 连字符 /
# 以及 Windows 文件名非法字符（不同来源常拿它们互相替换）。
_MATCH_NOISE = str.maketrans("", "", ' \t\u3000_-:*?"<>|')


def now_ts() -> float:
    """当前 Unix 时间戳（秒）。"""
    return time.time()


def numeric_id(seed: str) -> str:
    """由任意字符串派生一个**稳定的数字短 ID**（最多 9 位）。

    群成员看到的是「文件ID：320409845」这种纯数字，输入方便；
    派生规则是确定性的（md5 前 8 个 hex 位取模），重启后不变。

    本插件用 ``numeric_id(f"lz_{fid}")`` 给蓝奏云文件派生「文件ID」，
    与 fid 一一对应、又不直接暴露真实的 fid。
    """
    digest = hashlib.md5(str(seed).encode("utf-8")).hexdigest()  # noqa: S324 - 非安全用途
    return str(int(digest[:8], 16) % 10**9)


def normalize_name(text: str) -> str:
    """把文件名归一化，用于容错匹配。

    不同来源（蓝奏云网页 / 用户手输 / 群里贴的文件名）对同一个文件的写法可能
    不同：空格 vs 下划线、全角空格、连字符、Windows 非法字符替换等。匹配时把
    这些差异字符全部剥掉再比较，因此「朗朗 晴天.zip」也能命中「朗朗晴天.zip」。
    """
    return (text or "").lower().translate(_MATCH_NOISE)


def human_size(num: int | float) -> str:
    """把字节数格式化为易读字符串。"""
    try:
        value = float(num)
    except (TypeError, ValueError):
        return "0 B"
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units[:-1]:
        if abs(value) < 1024.0:
            break
        value /= 1024.0
    else:
        unit = units[-1]
    if unit == "B":
        return f"{int(value)} B"
    return f"{value:.1f} {unit}"
