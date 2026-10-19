"""报告引擎（阶段 6）：Jinja2 模板 -> Markdown / HTML 双输出。

模板强制结构：摘要、分级标题、要点列表、数据对比表、图表、配图。
MVP 输出 Markdown（主交付）+ 轻量 HTML（便于浏览器预览）。
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
OUT_DIR = Path(__file__).resolve().parent.parent / "output"


def _build_summary(metrics: list[dict], limit: int = 5) -> list[str]:
    out = []
    for m in metrics[:limit]:
        yoy = f"，同比 {m.get('yoy')}" if m.get("yoy") else ""
        out.append(
            f"{m.get('subject','')}{m.get('event','')} {m.get('value')} {m.get('unit','')}"
            f"（来源：{m.get('source','')}{yoy}）"
        )
    return out


def _md_to_html(md: str) -> str:
    """极简 Markdown -> HTML，仅覆盖本模板用到的语法。"""
    lines = md.splitlines()
    html, i = [], 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("## "):
            html.append(f"<h2>{line[3:]}</h2>")
        elif line.startswith("# "):
            html.append(f"<h1>{line[2:]}</h1>")
        elif line.startswith("### "):
            html.append(f"<h3>{line[4:]}</h3>")
        elif line.startswith("!["):
            mm = re.match(r"!\[(.*?)\]\((.*?)\)", line)
            if mm:
                html.append(f'<p><img src="{mm.group(2)}" alt="{mm.group(1)}" style="max-width:100%"></p>')
        elif line.strip().startswith("- "):
            items = []
            while i < len(lines) and lines[i].strip().startswith("- "):
                items.append(f"<li>{lines[i].strip()[2:]}</li>")
                i += 1
            html.append(f"<ul>{''.join(items)}</ul>")
            continue
        elif line.strip().startswith("|") and i + 1 < len(lines) and lines[i + 1].strip().startswith("|"):
            # 表格
            def parse_row(r):
                return [c.strip() for c in r.strip().strip("|").split("|")]

            header = parse_row(line)
            i += 2  # 跳过分隔行
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(parse_row(lines[i]))
                i += 1
            thead = "".join(f"<th>{h}</th>" for h in header)
            tbody = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
            html.append(f"<table border='1' cellspacing='0' cellpadding='6' style='border-collapse:collapse'>{thead}{tbody}</table>")
            continue
        elif line.strip() == "":
            pass
        else:
            html.append(f"<p>{line}</p>")
        i += 1
    return "\n".join(html)


def render_report(
    metrics: list[dict],
    charts: list[dict],
    stats: dict,
    sources: list[str],
    used_sample: bool,
    date: str | None = None,
) -> dict:
    date = date or datetime.now().strftime("%Y-%m-%d")
    day_dir = OUT_DIR / date
    day_dir.mkdir(parents=True, exist_ok=True)

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("daily_report.md.j2")

    ctx = {
        "date": date,
        "summary": _build_summary(metrics),
        "sources": sources,
        "stats": stats,
        "metrics": metrics,
        "charts": charts,
        "used_sample": used_sample,
    }
    md = template.render(**ctx)

    md_path = day_dir / f"日报_{date}.md"
    html_path = day_dir / f"日报_{date}.html"
    md_path.write_text(md, encoding="utf-8")
    html_content = (
        f"<!doctype html><html lang='zh'><head><meta charset='utf-8'>"
        f"<title>压铸行业情报日报 {date}</title>"
        f"<style>body{{font-family:system-ui,'Microsoft YaHei',sans-serif;max-width:900px;margin:24px auto;padding:0 16px;color:#222}}"
        f"h1{{border-bottom:2px solid #3b7dd8;padding-bottom:6px}}h2{{margin-top:28px;color:#1f4e79}}"
        f"table{{border-collapse:collapse;margin:12px 0}}th,td{{border:1px solid #ccc;padding:6px 10px}}"
        f"th{{background:#f0f6ff}}li{{line-height:1.7}}</style></head><body>{_md_to_html(md)}</body></html>"
    )
    html_path.write_text(html_content, encoding="utf-8")

    return {"md": str(md_path), "html": str(html_path), "date": date}
