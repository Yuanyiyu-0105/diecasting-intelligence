"""清洗层（阶段 2/5）：从原始 HTML 抽正文。

优先 trafilatura（去导航/广告保留正文），失败回退 BeautifulSoup <p> 拼接。
样例数据本身已是干净正文，直接返回。
"""
from __future__ import annotations

from collectors.web_collector import RawItem


def clean_item(item: RawItem) -> str:
    # 样例数据或已提取好的正文直接返回，省一次解析
    if item.content and len(item.content.strip()) > 30:
        return item.content.strip()
    if not item.raw_html:
        return item.content.strip()
    try:
        import trafilatura

        text = trafilatura.extract(item.raw_html, include_comments=False, include_tables=False)
        if text and len(text) > 30:
            return text.strip()
    except Exception:  # noqa: BLE001
        pass
    # 回退：BeautifulSoup 提取 <p>
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(item.raw_html, "lxml")
        paras = [p.get_text(strip=True) for p in soup.select("p") if p.get_text(strip=True)]
        text = "\n".join(paras)
        if text:
            return text
    except Exception:  # noqa: BLE001
        pass
    return item.content.strip()


def clean_all(items: list[RawItem]) -> list[tuple[RawItem, str]]:
    return [(it, clean_item(it)) for it in items]
