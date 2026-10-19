"""OICA 采集器（阶段 1：真实抓取 sales-statistics 页，解析各国销量表）。

真实路径：抓 sales-statistics 页面 -> 定位「World Motor Vehicle - Sales - 年份」表格
         -> 提取各国销量 -> 归一为「万辆」并写成中文句子（便于下游抽取/阅读）。
MVP 健壮性：页面结构变化 / 网络不可达时，回退到内置样例数据，
保证整条流水线在离线环境也能跑出第一份报告。
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .web_collector import BaseConnector, RawItem

OICA_URL = "https://www.oica.net/sales-statistics/"

# 国家英文名 -> 中文名（仅用于读数展示，不影响抽取）
_COUNTRY_CN = {
    "CHINA": "中国", "USA": "美国", "UNITED STATES": "美国", "JAPAN": "日本",
    "GERMANY": "德国", "INDIA": "印度", "BRAZIL": "巴西", "FRANCE": "法国",
    "UK": "英国", "UNITED KINGDOM": "英国", "ITALY": "意大利", "CANADA": "加拿大",
}

# 内置样例：结构、口径与真实抓取结果一致，便于离线演示与验收
OICA_SAMPLE = [
    RawItem(
        source="oica",
        url="https://www.oica.net/sales-statistics/",
        title="2025 年全球汽车产量与销量统计",
        publish_time="2026-01-15",
        content=(
            "根据 OICA 最新数据，2025 年全球汽车产量达到 9200 万辆，同比增长 3.5%。"
            "其中中国市场产量约 2700 万辆，占全球总量的 29.3%。"
            "2025 年全球汽车销量为 8918 万辆，较 2024 年的 8910 万辆基本持平。"
            "新能源汽车在全球销量中占比持续提升，达到约 1800 万辆。"
        ),
        is_sample=True,
    ),
]


def _parse_sales(html: str):
    """从 OICA 页面解析最新一年的全球分国别销量，返回 (年份, 中文综述文本) 或 None。"""
    soup = BeautifulSoup(html, "lxml")
    blocks = []
    for h in soup.find_all(["h2", "h3"]):
        title = h.get_text(" ", strip=True)
        if "World Motor Vehicle" not in title or "Sales" not in title:
            continue
        tbl = h.find_next("table")
        if not tbl:
            continue
        m = re.search(r"(20\d{2})", title)
        year = int(m.group(1)) if m else 0
        rows = []
        for tr in tbl.find_all("tr"):
            cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) >= 2 and re.search(r"\d", cells[-1]):
                rows.append((cells[0], cells[-1]))
        if rows:
            blocks.append((year, rows))
    if not blocks:
        return None
    year, rows = max(blocks, key=lambda b: b[0])

    items, total = [], 0
    for name, val in rows:
        digits = re.sub(r"[,\s]", "", val)
        try:
            num = int(digits)
        except ValueError:
            continue
        total += num
        cn = _COUNTRY_CN.get(name.upper(), name.title())
        items.append(f"{cn}市场销量约 {num / 10000:.1f} 万辆")
    if not items:
        return None
    text = f"根据 OICA 最新数据，{year} 年全球汽车销量达到 {total / 10000:.1f} 万辆。"
    text += "其中" + "、".join(items[:6]) + "。"
    return year, text


class OicaConnector(BaseConnector):
    name = "oica"
    url = OICA_URL

    def crawl(self) -> list[RawItem]:
        try:
            html = self._fetch(self.url)
            parsed = _parse_sales(html)
            if not parsed:
                raise ValueError("OICA 未解析到销量表（疑似页面结构变化）")
            year, text = parsed
            return [
                RawItem(
                    source="oica",
                    url=self.url,
                    title=f"OICA {year} 全球汽车销量",
                    publish_time=str(year),
                    content=text,
                )
            ]
        except Exception as e:  # noqa: BLE001
            if self.use_sample_on_fail:
                print(f"[oica] 实时抓取失败（{e}），回退样例数据")
                return list(OICA_SAMPLE)
            raise
