"""相关性过滤（阶段 3）：规则初筛 + LLM 二判钩子。

MVP 先实现规则初筛（快速、零成本）。LLM 二判作为钩子保留：
边界样本（得分落在 0.3~0.6）可送 DeepSeek 裁决，这里默认仅规则。
返回 (kept, score, reason)。
"""
from __future__ import annotations

from config.keywords import POSITIVE, NEGATIVE_HINTS


def _hit_count(text: str) -> tuple[int, list[str]]:
    hits = [k for k in POSITIVE if k in text]
    return len(hits), hits


def is_relevant(text: str, threshold: float = 0.0) -> tuple[bool, float, str]:
    """规则判定：命中正样本关键词即保留。返回 (是否保留, 得分, 原因)。"""
    if not text or len(text) < 5:
        return False, 0.0, "正文为空"

    n_hits, hits = _hit_count(text)
    neg_hits = [k for k in NEGATIVE_HINTS if k in text]

    score = min(1.0, n_hits / 3.0)  # 命中 3 个及以上正样本视为满分
    reason = f"命中正样本关键词 {n_hits} 个：{hits}"

    # 强负信号且无正样本 -> 直接过滤
    if neg_hits and n_hits == 0:
        return False, 0.0, f"负样本强信号 {neg_hits} 且无压铸相关关键词"

    kept = n_hits > 0 and score >= threshold
    return kept, score, reason
