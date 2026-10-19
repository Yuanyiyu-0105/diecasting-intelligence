import argparse
import base64
import html
import io
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from processors.news_processor import CATEGORIES

CACHE_PATH = "data/clean/analysis.json"
BASE_CN = {"units": "辆/台", "CNY": "元", "USD": "美元", "EUR": "欧元",
           "JPY": "日元", "t": "吨", "%": "%"}

CSS = """
body{font-family:"Microsoft YaHei",Arial,sans-serif;max-width:900px;margin:30px auto;padding:0 16px;color:#222;line-height:1.7}
h1{border-bottom:3px solid #1f4e79;padding-bottom:8px}
h2{color:#1f4e79;margin-top:36px;border-left:5px solid #1f4e79;padding-left:10px}
h3{margin-bottom:4px}
.summary{background:#f1f6fb;border-radius:8px;padding:12px 18px}
.meta{color:#777;font-size:13px;margin:0 0 6px}
.card{border-bottom:1px solid #e5e5e5;padding:10px 0}
.card img{max-width:100%;max-height:260px;border-radius:6px;margin:6px 0}
table{border-collapse:collapse;width:100%;font-size:14px;margin:10px 0}
th,td{border:1px solid #ccc;padding:6px 8px;text-align:left}
th{background:#1f4e79;color:#fff}
.chart{text-align:center;margin:14px 0}
.chart img{max-width:100%}
.small{color:#888;font-size:12px}
"""


def esc(s):
    return html.escape(str(s if s is not None else ""))


def parse_dt(s):
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return datetime.min


# ---------- 图表 ----------
def _plt():
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    return plt


def _to_b64(plt, fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def display_scale(values, base):
    unit = BASE_CN.get(base, base)
    if base == "%":
        return 1, "%"
    m = max(abs(v) for v in values)
    if m >= 1e8:
        return 1e8, "亿" + unit
    if m >= 1e4:
        return 1e4, "万" + unit
    return 1, unit


def line_chart(name, base, points):
    plt = _plt()
    if plt is None:
        return None
    periods = [p for p, _ in points]
    vals = [v for _, v in points]
    div, label = display_scale(vals, base)
    ys = [v / div for v in vals]
    fig, ax = plt.subplots(figsize=(7, 3.4))
    ax.plot(periods, ys, marker="o")
    for x, y in zip(periods, ys):
        ax.annotate(f"{y:,.2f}", (x, y), textcoords="offset points",
                    xytext=(0, 7), ha="center", fontsize=8)
    ax.set_title(name)
    ax.set_ylabel(label)
    ax.grid(alpha=0.3)
    plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
    return _to_b64(plt, fig)


def bar_chart(name, labels, values, base="units"):
    """横向条形图:用于单期间的国家对比(如 2025 各国汽车销量)。"""
    plt = _plt()
    if plt is None or not values:
        return None
    div, label = display_scale(values, base)
    ys = [v / div for v in values]
    fig, ax = plt.subplots(figsize=(7, 0.42 * len(ys) + 1.4))
    pos = range(len(ys))
    ax.barh(list(pos), ys)
    ax.set_yticks(list(pos))
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    for y, v in zip(pos, ys):
        ax.annotate(f"{v:,.2f}", (v, y), textcoords="offset points",
                    xytext=(4, 0), va="center", fontsize=8)
    ax.set_title(name)
    ax.set_xlabel(label)
    ax.grid(axis="x", alpha=0.3)
    return _to_b64(plt, fig)


def pie_chart(title, counts):
    plt = _plt()
    if plt is None or not counts:
        return None
    fig, ax = plt.subplots(figsize=(5, 3.6))
    ax.pie(list(counts.values()), labels=list(counts.keys()), autopct="%1.0f%%")
    ax.set_title(title)
    return _to_b64(plt, fig)


def img_tag(b64):
    return f'<div class="chart"><img src="data:image/png;base64,{b64}"></div>' if b64 else ""


# ---------- 报告 ----------
def fmt_num(v):
    return f"{v:,.0f}" if abs(v) >= 100 else f"{v:,.2f}"


def _warn_llm_inactive(title, *lines):
    print("\n" + "=" * 62 + f"\n[警告] {title}")
    for ln in lines:
        print("  " + ln)
    print("=" * 62 + "\n")


def _fallback_cache(store):
    """从新闻库造一份「仅预筛」的分析结果,让报告在无 LLM 时也能出。"""
    cache = {}
    for url, a in store.items():
        cache[url] = {
            "url": url, "title": a.get("title", ""), "media": a.get("media", ""),
            "status": a.get("status", ""), "image_url": a.get("image_url", ""),
            "first_seen": a.get("first_seen", ""),
            "analysis_basis": a.get("analysis_basis", ""),
            "category": a.get("category", "其他"),
            "summary_zh": (a.get("content", "") or "")[:120],
            "bullets": [], "event": {}, "metrics": [],
            "relevant": a.get("prefilter_pass", True),
            "relevance_reason": "关键词预筛(未调用 LLM)",
        }
    return cache


def load_cache():
    """读 analysis.json;缺失或覆盖率过低时整体降级为「仅预筛」。

    只判断文件是否存在是不够的:llm_analyzer 在 Key 无效时可能已经写了
    几条「预筛不通过」的记录,此时报告会从 100+ 条塌成几条。
    因此这里再校验一次覆盖率,不足一半就整体降级,保证报告完整。
    """
    from processors.store import load_store
    store = load_store()

    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            cache = json.load(f)
        covered = sum(1 for u in store if u in cache)
        ratio = (covered / len(store)) if store else 1.0
        if ratio >= 0.5:
            return cache, True
        _warn_llm_inactive(
            f"LLM 分析覆盖率仅 {covered}/{len(store)}({ratio:.0%}),未达一半",
            "原因:.env 的 DEEPSEEK_API_KEY 无效或余额不足,llm_analyzer 已中止",
            "处理:本次降级为「仅预筛」报告,基于完整新闻库",
            "修复:更新 .env 的有效 key 后重跑 analyzers/llm_analyzer.py",
        )
        return _fallback_cache(store), False

    _warn_llm_inactive(
        f"LLM 语义分析未生效 —— 未找到 {CACHE_PATH}",
        "原因1:.env 里没有 DEEPSEEK_API_KEY(步骤「LLM分析」会直接退出)",
        "原因2:analyzers/llm_analyzer.py 中途报错,见 logs/pipeline_*.log",
        "影响:报告只有关键词预筛,没有语义相关度判定与中文摘要",
        "修复:在 .env 填入有效 key 后,重跑 analyzers/llm_analyzer.py",
    )
    return _fallback_cache(store), False


def build_report(days):
    cache, analyzed = load_cache()

    cutoff = datetime.now() - timedelta(days=days)
    period = [x for x in cache.values() if parse_dt(x.get("first_seen")) >= cutoff]
    kept = [x for x in period if x["relevant"]]
    dropped = [x for x in period if not x["relevant"]]

    # 趋势序列:用全部历史相关条目累积,(指标, 基础单位) -> {期间: 值}
    series = defaultdict(dict)
    for x in cache.values():
        if not x["relevant"]:
            continue
        for m in x.get("metrics", []):
            if m["unit_recognized"] and m["period"]:
                series[(m["name"], m["base_unit"])][m["period"]] = m["value_normalized"]

    by_cat = defaultdict(list)
    for x in kept:
        by_cat[x["category"]].append(x)
    counts = {c: len(by_cat[c]) for c in CATEGORIES if by_cat[c]}

    today = datetime.now().strftime("%Y-%m-%d")
    parts = [f"<h1>压铸行业全链路情报日报 · {today}</h1>"]
    if not analyzed:
        parts.append(
            '<div style="border-left:4px solid #e0a800;background:#fff8e1;'
            'padding:10px 14px;margin:12px 0;border-radius:4px">'
            '<b>⚠ LLM 语义分析未生效</b><br>'
            '本次报告仅经关键词预筛,未做语义相关度判定,摘要取自原文片段。'
            '请在 .env 中配置 DEEPSEEK_API_KEY 后重跑 analyzers/llm_analyzer.py。'
            '</div>'
        )

    # 摘要
    parts.append('<h2>一、摘要</h2><div class="summary">')
    parts.append(f"<p>统计区间:近 {days} 天。共采集 {len(period)} 条,"
                 f"经相关性过滤保留 <b>{len(kept)}</b> 条,剔除 {len(dropped)} 条。</p>")
    if kept:
        parts.append("<ul>")
        for c in CATEGORIES:
            for x in by_cat.get(c, [])[:3]:
                parts.append(f"<li>【{esc(c)}】{esc(x['summary_zh'])}</li>")
        parts.append("</ul>")
    else:
        parts.append("<p>本期没有与压铸产业链相关的内容。</p>")
    parts.append("</div>")
    parts.append(img_tag(pie_chart("保留情报类别占比", counts)))

    # 分类详情
    parts.append("<h2>二、行业动态</h2>")
    for c in CATEGORIES:
        items = by_cat.get(c, [])
        if not items:
            continue
        parts.append(f"<h2 style='font-size:18px'>{esc(c)}</h2>")
        for x in items:
            ev = x.get("event") or {}
            parts.append('<div class="card">')
            parts.append(f'<h3><a href="{esc(x["url"])}" target="_blank">{esc(x["title"])}</a></h3>')
            parts.append(f'<p class="meta">{esc(x["media"])} · {esc(x["first_seen"][:10])}</p>')
            if x.get("image_url"):
                parts.append(f'<img src="{esc(x["image_url"])}" referrerpolicy="no-referrer" alt="">')
            parts.append(f"<p>{esc(x['summary_zh'])}</p>")
            if x["bullets"]:
                parts.append("<ul>" + "".join(f"<li>{esc(b)}</li>" for b in x["bullets"]) + "</ul>")
            facts = []
            if ev.get("date"):
                facts.append(f"时间:{esc(ev['date'])}")
            if ev.get("subject"):
                facts.append(f"主体:{esc(ev['subject'])}")
            if ev.get("amount") is not None:
                facts.append(f"规模:{esc(ev['amount'])} {esc(ev.get('amount_unit'))}")
            if facts:
                parts.append(f'<p class="small">{" ｜ ".join(facts)}</p>')
            if x.get("basis") == "title_only":
                parts.append('<p class="small">⚠ 正文未获取(可能为付费墙),仅依据标题判断</p>')
            parts.append("</div>")

    # 数据对比表
    rows = [(x, m) for x in kept for m in x.get("metrics", [])]
    parts.append("<h2>三、数据对比</h2>")
    if rows:
        parts.append("<table><tr><th>指标</th><th>期间</th><th>原文数值</th>"
                     "<th>标准化数值</th><th>来源</th></tr>")
        for x, m in rows:
            if m["unit_recognized"]:
                norm = f"{fmt_num(m['value_normalized'])} {esc(BASE_CN.get(m['base_unit'], m['base_unit']))}"
            else:
                norm = "单位未识别"
            parts.append(
                f"<tr><td>{esc(m['name'])}</td><td>{esc(m['period'])}</td>"
                f"<td>{fmt_num(m['value'])} {esc(m['unit'])}</td><td>{norm}</td>"
                f'<td><a href="{esc(x["url"])}" target="_blank">{esc(x["media"])}</a></td></tr>')
        parts.append("</table>")
    else:
        parts.append("<p>本期未抽取到数值数据。</p>")

    # 趋势图
    parts.append("<h2>四、趋势图表</h2>")
    drawn = False
    for (name, base), pts in series.items():
        if len(pts) >= 2:
            tag = img_tag(line_chart(name, base, sorted(pts.items())))
            if tag:
                parts.append(tag)
                drawn = True
    if not drawn:
        parts.append("<p>同一指标的数据点不足 2 个,暂无法绘制趋势图"
                     "(随每日采集累积后自动生成)。</p>")

    # OICA 等统计机构指标库(数值与原表逐字一致,是报告里最硬的数据)
    try:
        from processors.store import load_metrics
        metrics = list(load_metrics().values())
    except Exception:
        metrics = []
    if metrics:
        parts.append('<h2>五、权威统计(采集自机构官网)</h2>')
        by_period = defaultdict(lambda: defaultdict(list))
        for m in metrics:
            by_period[m.get("period", "")][m.get("source", "")].append(m)

        for period in sorted(by_period, reverse=True):
            parts.append(f"<h3>{esc(period)}</h3>")
            for src in sorted(by_period[period]):
                group = by_period[period][src]
                parts.append(f'<p class="small">来源:{esc(src)} ({len(group)} 条)</p>')
                # 同口径的汽车销量/产量做横向对比图
                total = [m for m in group if m["name"].endswith(("汽车销量", "汽车产量"))]
                if len(total) >= 2:
                    total.sort(key=lambda m: -m["value"])
                    tag = img_tag(bar_chart(
                        f"{period} 各国汽车销量/产量",
                        [m["name"].replace("汽车销量", "").replace("汽车产量", "")
                         for m in total],
                        [m["value"] for m in total], "units"))
                    if tag:
                        parts.append(tag)
                parts.append("<table><tr><th>指标</th><th>数值</th>"
                             "<th>标准化</th><th>备注</th></tr>")
                for m in sorted(group, key=lambda x: x["name"]):
                    norm = (f"{fmt_num(m['value_normalized'])} "
                            f"{esc(BASE_CN.get(m.get('base_unit'), m.get('base_unit')))}"
                            ) if m.get("unit_recognized") else "单位未识别"
                    parts.append(f"<tr><td>{esc(m['name'])}</td>"
                                 f"<td>{fmt_num(m['value'])} {esc(m.get('unit'))}</td>"
                                 f"<td>{norm}</td>"
                                 f"<td>{esc((m.get('note') or '')[:30])}</td></tr>")
                parts.append("</table>")
        parts.append('<p class="small">以上数值由采集程序直接解析机构官网表格/财报,'
                     '未经大模型改写,可与原文逐字核对。</p>')

    # 降噪明细
    parts.append("<h2>附:已剔除条目(供降噪核对)</h2>")
    if dropped:
        parts.append("<table><tr><th>标题</th><th>媒体</th><th>剔除原因</th></tr>")
        for x in dropped:
            parts.append(f"<tr><td>{esc(x['title'])}</td><td>{esc(x['media'])}</td>"
                         f"<td>{esc(x['relevance_reason'])}</td></tr>")
        parts.append("</table>")
    parts.append(f'<p class="small">生成时间:{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>')

    doc = (f'<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">'
           f'<title>压铸行业情报日报 {today}</title><style>{CSS}</style></head>'
           f'<body>{"".join(parts)}</body></html>')

    os.makedirs("reports", exist_ok=True)
    path = f"reports/industry_report_{datetime.now().strftime('%Y%m%d')}.html"
    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)
    print("报告已生成:", path)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1, help="报告统计最近几天")
    args = ap.parse_args()
    build_report(args.days)