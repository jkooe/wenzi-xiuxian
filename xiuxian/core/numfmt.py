"""大数格式化：修为/属性数值随境界指数膨胀（炼气 100 -> 混元 1.2e19），
直接打印 60000000000 这类长串不可读。统一用中文单位：万 / 亿 / 万亿 / 兆 / 京。

    fmt_num(100)       -> "100"
    fmt_num(12345)     -> "1.2万"
    fmt_num(6e9)       -> "60亿"
    fmt_num(3e17)      -> "30兆"
    fmt_num(1.2e19)    -> "1200兆"

纯展示层：只改显示，不改任何数值/判定。逻辑可解释性不受影响。
"""

from __future__ import annotations

# (阈值, 单位)：从大到小匹配，取第一个 >= 阈值的档位
_UNITS: tuple[tuple[float, str], ...] = (
    (1e20, "京"),
    (1e16, "兆"),
    (1e12, "万亿"),
    (1e8, "亿"),
    (1e4, "万"),
)


def fmt_num(n: float | int, ndigits: int = 1) -> str:
    """把数值格式化为可读的中文单位字符串（1 位小数，去尾零）。"""
    if n is None:
        return ""
    n = float(n)
    if n < 0:
        return "-" + fmt_num(-n, ndigits)
    if n < 1e4:
        return str(int(round(n))) if abs(n - round(n)) < 0.5 else f"{n:.1f}"
    for unit_value, unit in _UNITS:
        if n >= unit_value:
            v = n / unit_value
            s = f"{v:.{ndigits}f}".rstrip("0").rstrip(".")
            return s + unit
    return str(int(round(n)))
