"""采集层基类：统一 RawItem 数据结构 + 通用网页抓取引擎。

- RawItem：所有采集器统一产出的数据结构，下游完全不关心数据来自哪个源。
- collect_webpage()：通用网页抓取函数（用户自写版本，带 URL 校验 + 正文清理）。
- BaseConnector：采集器模板，crawl() 由子类实现，_fetch() 复用 collect_webpage。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup

# 请求头：声明是普通浏览器客户端，降低被反爬拦截的概率
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
}


@dataclass
class RawItem:
    source: str
    url: str
    title: str = ""
    publish_time: Optional[str] = None
    content: str = ""
    images: list = field(default_factory=list)
    crawl_time: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    raw_html: str = ""
    is_sample: bool = False  # 标记是否回退样例数据，报告中会显式说明

    def to_dict(self) -> dict:
        return asdict(self)


def collect_webpage(url: str, timeout: int = 20) -> dict:
    """获取一个网页，并提取标题和正文文本。

    参数：
        url: 需要采集的网页地址
        timeout: 请求超时秒数
    返回：
        包含网页标题、正文、网址、状态码和采集时间的字典。
        额外带 "html" 键返回原始 HTML，供需要解析表格的子类使用。
    """
    # 1. 检查网址格式
    parsed_url = urlparse(url)
    if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
        raise ValueError("请输入有效的 HTTP 或 HTTPS 网页地址")

    # 2. 请求网页
    response = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
    response.raise_for_status()  # HTTP 状态异常（如 404）时抛错
    # 让 requests 用更准的编码猜测，避免中文乱码
    response.encoding = response.apparent_encoding or "utf-8"

    # 3. 解析 HTML
    soup = BeautifulSoup(response.text, "html.parser")

    # 4. 提取网页标题
    title = soup.title.get_text(" ", strip=True) if soup.title else ""

    # 5. 移除通常不属于正文的元素
    for element in soup.select("script, style, nav, footer, header, noscript"):
        element.decompose()

    # 6. 提取页面文本
    text = soup.get_text(" ", strip=True)

    # 7. 返回结构化数据
    return {
        "url": response.url,
        "title": title,
        "content": text,
        "html": response.text,  # 原始 HTML，供解析表格/段落的子类使用
        "status_code": response.status_code,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


class BaseConnector:
    name = "base"
    url = ""

    def __init__(self, timeout: int = 15, use_sample_on_fail: bool = True):
        self.timeout = timeout
        self.use_sample_on_fail = use_sample_on_fail

    def crawl(self) -> list[RawItem]:
        raise NotImplementedError

    def _fetch(self, url: str) -> str:
        """调用统一抓取引擎 collect_webpage，返回原始 HTML 文本（供子类解析）。"""
        return collect_webpage(url, timeout=self.timeout)["html"]


if __name__ == "__main__":
    # 使用一个公开测试网页验证程序
    test_url = "https://example.com"
    try:
        article = collect_webpage(test_url)
        print("采集成功！")
        print("网页标题：", article["title"])
        print("网页地址：", article["url"])
        print("HTTP 状态码：", article["status_code"])
        print("采集时间：", article["fetched_at"])
        print("正文预览：", article["content"][:300])
    except requests.RequestException as exc:
        print("网页请求失败：", exc)
    except ValueError as exc:
        print("输入错误：", exc)
