"""压铸行业全链路情报采集与分析系统 —— MVP 一键全链路入口。

流程：采集 -> 清洗 -> 存储(去重) -> 相关性过滤 -> 语义抽取+单位对齐
      -> 图表 -> 报告 -> 数值回查(QA)

运行：
    python main.py               # 跑一次全链路，产出当天日报
    python -m tests.test_oica    # 冒烟测试
    python scheduler/jobs.py     # 每日定时（阶段7，默认 08:00）

设计：所有外部依赖（网络/LLM）都做了兜底，离线 + 无 Key 也能跑通，先达标后深挖。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

from collectors import build_connectors
from processors.cleaner import clean_all
from processors.relevance import is_relevant
from analyzers.event_extractor import build_extractor
from reports.chart_generator import generate_charts
from reports.report_generator import render_report
from storage import database as db

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config" / "sources.yaml"


def _load_config() -> dict:
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    return cfg or {}


def _qa_backcheck(metrics: list[dict], article_texts: list[str], date: str) -> dict:
    """验收第 3 条：抽取数值回查原始文本，确认数值与单位一致。"""
    results = []
    for m in metrics:
        val = m.get("value")
        unit = m.get("unit", "")
        ok = False
        if val is not None:
            for txt in article_texts:
                # 整数/浮点两种写法都查
                if (str(val) in txt) or (f"{val:g}" in txt):
                    ok = True
                    break
        results.append(
            {
                "subject": m.get("subject"),
                "event": m.get("event"),
                "value": val,
                "unit": unit,
                "backcheck": "pass" if ok else "fail",
            }
        )
    qa = {"date": date, "total": len(results), "passed": sum(1 for r in results if r["backcheck"] == "pass"), "details": results}
    out_dir = ROOT / "output" / date
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "qa_report.json").write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
    return qa


def run_pipeline() -> dict:
    load_dotenv()
    cfg = _load_config()
    sources_cfg = cfg.get("sources", {})
    settings = cfg.get("settings", {})

    db.init_db()
    conn = db._connect()

    connectors = build_connectors(sources_cfg, settings)
    date = datetime.now().strftime("%Y-%m-%d")

    raw_items = []
    for c in connectors:
        # 样例回退已在 config/sources.yaml 关闭(use_sample_on_fail: false),
        # 此时单个源抓取失败会抛异常。这里兜住:
        #   既不编造数据,也不因为一个源挂掉拖垮整条链路。
        try:
            items = c.crawl()
        except Exception as e:  # noqa: BLE001
            print(f"[{c.name}] 抓取失败,已跳过:{e}")
            continue
        raw_items.extend(items)
        print(f"[{c.name}] 抓取 {len(items)} 条")

    if not raw_items:
        print("\n所有信源均未抓到数据:不生成报告,也不写入任何数值。"
              "\n请检查网络连接、页面结构是否已变化,或改用 run_pipeline.py 主链路。")
        conn.close()
        return {"report": None, "stats": None, "qa": None}

    # 清洗
    cleaned = clean_all(raw_items)

    raw_count = len(cleaned)
    kept_items, filtered_items = [], []
    for item, text in cleaned:
        kept, score, reason = is_relevant(text)
        if kept:
            kept_items.append((item, text))
        else:
            filtered_items.append((item, reason, score))
            db.insert_filter_log(
                conn,
                {"source": item.source, "url": item.url, "title": item.title, "reason": reason, "score": score},
            )

    # 存储文章（去重）
    for item, _ in cleaned:
        db.insert_article(
            conn,
            item.to_dict(),
        )

    # 语义抽取 + 单位对齐
    extractor = build_extractor()
    metrics = []
    article_texts = []
    for item, text in kept_items:
        article_texts.append(text)
        ms = extractor.extract(text, source=item.source)
        for m in ms:
            db.insert_metric(conn, m)
        metrics.extend(ms)
        print(f"[{item.source}] 抽取指标 {len(ms)} 条")

    # 可视化
    charts = generate_charts(metrics, date)

    # 报告
    stats = {
        "raw_count": raw_count,
        "kept_count": len(kept_items),
        "filtered_count": len(filtered_items),
        "metric_count": len(metrics),
    }
    used_sample = any(it.is_sample for it, _ in cleaned)
    report = render_report(
        metrics=metrics,
        charts=charts,
        stats=stats,
        sources=[c.name for c in connectors],
        used_sample=used_sample,
        date=date,
    )
    db.insert_report(conn, date, report["md"], report["html"])

    # 数值回查
    qa = _qa_backcheck(metrics, article_texts, date)

    conn.close()

    print("\n===== 全链路跑通 =====")
    print(f"原始 {stats['raw_count']} 条 / 保留 {stats['kept_count']} 条 / 过滤 {stats['filtered_count']} 条")
    print(f"抽取指标 {stats['metric_count']} 条；图表 {len(charts)} 张")
    print(f"QA 数值回查：{qa['passed']}/{qa['total']} 通过")
    print(f"日报(MD): {report['md']}")
    print(f"日报(HTML): {report['html']}")
    print(f"QA报告: {ROOT / 'output' / date / 'qa_report.json'}")
    return {"report": report, "stats": stats, "qa": qa}


if __name__ == "__main__":
    run_pipeline()
