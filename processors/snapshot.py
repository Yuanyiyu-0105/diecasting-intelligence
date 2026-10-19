"""原始层按日快照 —— 为「原始数据连续 7 天」验收留证。

问题背景:
    各采集器此前要么完全不写 data/raw,要么写死固定文件名
    (如 data/raw/oica_news.json),每天跑会覆盖前一天,攒不出"连续 7 天"。

做法:
    在采集 + 抽取之后,把当天的新闻库与指标库按日期归档一份,
    并生成 manifest.json 记录各来源条数,验收时可按天核对。

用法:
    python processors/snapshot.py
"""
import json
import os
import shutil
import sys
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

NEWS_PATH = "data/clean/industry_news.json"
METRICS_PATH = "data/clean/metrics.json"
RAW_ROOT = "data/raw"


def _load(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _dump(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    out_dir = os.path.join(RAW_ROOT, today)
    os.makedirs(out_dir, exist_ok=True)

    news = _load(NEWS_PATH)
    metrics = _load(METRICS_PATH)

    # 归档当天快照(固定文件名,不做累积,方便按天回溯)
    for src, dst_name in ((NEWS_PATH, "industry_news.json"),
                          (METRICS_PATH, "metrics.json")):
        if os.path.exists(src):
            shutil.copyfile(src, os.path.join(out_dir, dst_name))

    manifest = {
        "date": today,
        "generated_at": datetime.now().isoformat(),
        "news_total": len(news),
        "metrics_total": len(metrics),
        "news_ok": sum(1 for x in news if x.get("status") == "ok"),
        "prefilter_pass": sum(1 for x in news if x.get("prefilter_pass")),
        "news_by_source": dict(Counter(x.get("source", "?") for x in news)),
        "metrics_by_source": dict(Counter(x.get("source", "?") for x in metrics)),
    }
    _dump(os.path.join(out_dir, "manifest.json"), manifest)

    # 汇总已归档天数,直观看是否攒够 7 天
    days = sorted(d for d in os.listdir(RAW_ROOT)
                  if os.path.isdir(os.path.join(RAW_ROOT, d)))

    print(f"快照已归档: {out_dir}")
    print(f"  新闻 {manifest['news_total']} 条"
          f"(正文成功 {manifest['news_ok']},预筛通过 {manifest['prefilter_pass']})")
    print(f"  指标 {manifest['metrics_total']} 条")
    for k, v in manifest["news_by_source"].items():
        print(f"    - {k}: {v}")
    print(f"\n已归档 {len(days)} 天: {', '.join(days)}")
    if len(days) < 7:
        print(f">>> 距离「连续 7 天」还差 {7 - len(days)} 天")


if __name__ == "__main__":
    main()
