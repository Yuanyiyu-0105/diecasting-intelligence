"""压铸行业情报可视化大屏（Flask + ECharts，暗色科技风）。

启动方式：
    .venv\\Scripts\\python.exe web/app.py
然后浏览器打开 http://127.0.0.1:5000

数据全部来自本地 JSON，不联网、不调用 LLM：
    data/clean/industry_news.json   新闻库
    data/clean/metrics.json         指标库
    data/raw/YYYY-MM-DD/            按日归档（7 天连续性验收）
"""
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime

from flask import Flask, jsonify, render_template

# 让脚本既能 `python web/app.py` 跑，也能被别的模块 import
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

NEWS_PATH = os.path.join(ROOT, "data", "clean", "industry_news.json")
METRIC_PATH = os.path.join(ROOT, "data", "clean", "metrics.json")
RAW_DIR = os.path.join(ROOT, "data", "raw")

app = Flask(__name__, template_folder="templates", static_folder="static")

TARGET_DAYS = 7  # 需求书验收：原始数据连续 7 天


# ---------------------------------------------------------------- 数据加载

def _load_json(path, default):
    """读 JSON，文件缺失或损坏时返回默认值而不是崩掉。"""
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[warn] 读取 {path} 失败: {e}")
        return default


def load_news():
    data = _load_json(NEWS_PATH, {})
    return list(data.values()) if isinstance(data, dict) else (data or [])


def load_metrics():
    data = _load_json(METRIC_PATH, {})
    return list(data.values()) if isinstance(data, dict) else (data or [])


def load_daily_snapshots():
    """扫描 data/raw/YYYY-MM-DD/ 目录，按日期升序返回。

    这是「原始数据（连续 7 天）」验收项的直接证据。
    """
    out = []
    if not os.path.isdir(RAW_DIR):
        return out
    for name in sorted(os.listdir(RAW_DIR)):
        d = os.path.join(RAW_DIR, name)
        if not (os.path.isdir(d) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", name)):
            continue
        news = _load_json(os.path.join(d, "industry_news.json"), [])
        mets = _load_json(os.path.join(d, "metrics.json"), [])
        mf = _load_json(os.path.join(d, "manifest.json"), {})
        out.append({
            "date": name,
            "news": len(news),
            "metrics": len(mets),
            "by_source": dict(mf.get("by_source", {})) if isinstance(mf, dict) else {},
        })
    return out


# ---------------------------------------------------------------- 工具

_PERIOD_RE = re.compile(r"(?P<y>20\d{2})(?:\s*[-年]?\s*(?P<q>[QH1-4])?)?")


def period_sort_key(period):
    """把混合格式的期间（2025 / 2026Q1 / 2025Q1-Q3 / 2026H1）变成可排序的元组。

    年度数据（无季度标记）排在同年季度之后，因为它是累计口径。
    """
    if not period:
        return (9999, 9)
    m = _PERIOD_RE.search(str(period))
    if not m:
        return (9999, 9)
    year = int(m.group("y"))
    q = (m.group("q") or "").upper()
    if q.startswith("Q"):          # 2026Q1 / 2025Q1-Q3
        # "2025Q1-Q3" 取最后一个数字作为期末
        nums = re.findall(r"Q(\d)", str(period))
        seq = int(nums[-1]) if nums else 1
    elif q.startswith("H"):        # 2026H1
        nums = re.findall(r"H(\d)", str(period))
        seq = int(nums[-1]) * 2 if nums else 2
    elif q.isdigit():              # 2026-08 月份
        nums = re.findall(r"(\d{1,2})", str(period).split("-")[-1])
        seq = int(nums[0]) if nums else 12
    else:                          # 纯年度 2025
        seq = 5
    return (year, seq)


def split_company_metric(name):
    """「旭升集团营业收入」-> ("旭升集团", "营业收入")"""
    for suffix in ("营业收入", "净利润", "营业总收入", "归母净利润"):
        if str(name).endswith(suffix):
            return str(name)[:-len(suffix)], suffix
    return str(name), "指标"


def fmt_value(v, unit):
    """数值按量级缩放，便于大屏阅读。"""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    unit = unit or ""
    a = abs(v)
    if a >= 1e8:
        return f"{v/1e8:.2f} 亿{unit}".replace("亿元", "元")
    if a >= 1e4:
        return f"{v/1e4:.2f} 万{unit}"
    return f"{v:,.0f} {unit}".strip()


# ---------------------------------------------------------------- 聚合

def build_payload():
    news, metrics = load_news(), load_metrics()
    snaps = load_daily_snapshots()

    # --- KPI ---
    days = len(snaps)
    kpi = {
        "news_total": len(news),
        "metric_total": len(metrics),
        "days": days,
        "days_target": TARGET_DAYS,
        "days_ok": days >= TARGET_DAYS,
        "source_count": len({n.get("source", "?") for n in news}),
        "prefilter_pass": sum(1 for n in news if n.get("prefilter_pass")),
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    # --- 信源分布 ---
    src_news = Counter(n.get("source", "未知") for n in news)
    src_metric = Counter(m.get("source", "未知") for m in metrics)

    # --- 每日采集趋势（7 天连续性）---
    daily = [{"date": s["date"], "news": s["news"], "metrics": s["metrics"]}
             for s in snaps]

    # --- 压铸上市企业营收 / 净利润趋势（巨潮数据）---
    fin = defaultdict(lambda: defaultdict(dict))   # {公司: {指标: {期间: 值}}}
    fin_unit = {}
    for m in metrics:
        if m.get("source") != "巨潮资讯网":
            continue
        company, kind = split_company_metric(m.get("name", ""))
        fin[company][kind][m.get("period", "")] = m.get("value_normalized") or m.get("value")
        fin_unit[kind] = m.get("unit", "")

    series_fin = []
    for company in sorted(fin):
        for kind in ("营业收入", "净利润"):
            pts = fin[company].get(kind) or {}
            if not pts:
                continue
            ordered = sorted(pts.items(), key=lambda kv: period_sort_key(kv[0]))
            series_fin.append({
                "company": company,
                "kind": kind,
                "periods": [p for p, _ in ordered],
                "values": [v for _, v in ordered],
                "unit": fin_unit.get(kind, ""),
            })

    # --- 权威统计（OICA / IDC / BCN）---
    stats = defaultdict(list)
    for m in metrics:
        if m.get("source") == "巨潮资讯网":
            continue
        stats[m.get("source", "未知")].append(m)

    stat_blocks = []
    for src in sorted(stats):
        items = sorted(stats[src], key=lambda x: (x.get("name", ""),
                                                  period_sort_key(x.get("period"))))
        stat_blocks.append({
            "source": src,
            "items": [{
                "name": i.get("name", ""),
                "period": i.get("period", ""),
                "display": fmt_value(i.get("value"), i.get("unit")),
                "note": i.get("note", ""),
            } for i in items],
        })

    # --- 最新新闻 ---
    latest = sorted(news, key=lambda n: n.get("first_seen", ""), reverse=True)[:12]

    return {
        "kpi": kpi,
        "source_news": dict(src_news),
        "source_metric": dict(src_metric),
        "daily": daily,
        "finance": series_fin,
        "stats": stat_blocks,
        "latest": [{
            "title": n.get("title", ""),
            "source": n.get("source", ""),
            "url": n.get("url", ""),
            "date": (n.get("first_seen") or "")[:10],
            "status": n.get("status", ""),
        } for n in latest],
    }


# ---------------------------------------------------------------- 路由

@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/api/data")
def api_data():
    return jsonify(build_payload())


@app.route("/health")
def health():
    return jsonify({"ok": True, "time": datetime.now().isoformat()})


if __name__ == "__main__":
    p = build_payload()
    print("=" * 58)
    print("  压铸行业情报可视化大屏")
    print(f"  新闻 {p['kpi']['news_total']} 条 / 指标 {p['kpi']['metric_total']} 条 "
          f"/ 已采集 {p['kpi']['days']}/{TARGET_DAYS} 天")
    print("  浏览器打开: http://127.0.0.1:5000")
    print("=" * 58)
    app.run(host="127.0.0.1", port=5000, debug=False)
