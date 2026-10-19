"""单位归一化规则表。

不同数据源单位口径不一致（万辆 vs 辆、亿元 vs 万元、千台 vs 台），
绘图前必须先统一到基准单位，否则图例单位混乱、数值不可比。
"""


# 目标基准单位 -> 换算系数（乘以该系数得到基准单位数值）
UNIT_TO_FACTOR = {
    "辆": 1,
    "台": 1,
    "万辆": 10000,
    "万台": 10000,
    "千台": 1000,
    "亿元": 100_000_000,
    "万元": 10_000,
    "元": 1,
    "%": 1,          # 百分比保持原样，只做标注
}


def normalize(value: float, unit: str):
    """返回 (基准数值, 基准单位)。无法识别的单位原样返回，避免静默出错。"""
    if unit in UNIT_TO_FACTOR:
        return value * UNIT_TO_FACTOR[unit], unit
    return value, unit


def normalize_label(unit: str) -> str:
    """用于在图表/报告里展示归一化后的口径，例如 '万辆' -> '辆'。"""
    return unit  # 基准单位本身即展示名；如需聚合到更大单位可在此扩展
