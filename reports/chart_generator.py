"""可视化（阶段 5）：根据抽取指标自动生成图表。

规则：按单位分组，同组内样本数 >= 2 生成柱状图（多主体对比）。
MVP 用 matplotlib 生成 PNG 落盘；生产可换 ECharts 交互式 HTML。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import matplotlib

matplotlib.use("Agg")  # 无界面环境必须
# 中文字体：Windows 优先微软雅黑，避免图表中文变方块（Glyph missing 警告）
matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "SimSun", "DengXian"]
matplotlib.rcParams["axes.unicode_minus"] = False  # 负号正常显示
import matplotlib.pyplot as plt

OUT_DIR = Path(__file__).resolve().parent.parent / "output"


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in name)


def generate_charts(metrics: list[dict], date: Optional[str] = None) -> list[dict]:
    date = date or datetime.now().strftime("%Y-%m-%d")
    day_dir = OUT_DIR / date
    day_dir.mkdir(parents=True, exist_ok=True)

    # 按单位分组
    groups: dict[str, list[dict]] = defaultdict(list)
    for m in metrics:
        groups[m.get("unit", "未知")].append(m)

    charts = []
    idx = 1
    for unit, items in groups.items():
        if len(items) < 2:
            continue
        labels = [f"{m.get('subject','')}/{m.get('event','')}" for m in items]
        values = [float(m.get("value", 0)) for m in items]
        title = f"{unit} 口径指标对比（{date}）"

        fig, ax = plt.subplots(figsize=(8, 4.5))
        bars = ax.bar(range(len(values)), values, color="#3b7dd8")
        ax.set_xticks(range(len(values)))
        ax.set_xticklabels(labels, rotation=20, ha="right", fontsize=8)
        ax.set_ylabel(unit)
        ax.set_title(title, fontsize=11)
        for b, v in zip(bars, values):
            ax.text(b.get_x() + b.get_width() / 2, v, f"{v:g}", ha="center", va="bottom", fontsize=8)
        fig.tight_layout()

        path = day_dir / f"chart_{idx}_{_safe(unit)}.png"
        fig.savefig(path, dpi=120)
        plt.close(fig)
        charts.append({"path": str(path), "title": title, "kind": "bar", "unit": unit})
        idx += 1

    if not charts:
        print("[visualize] 同单位样本不足 2 条，未生成图表（属正常，多日/多源累积后会出现）")
    return charts
