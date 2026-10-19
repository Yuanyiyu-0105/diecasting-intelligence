import re
import sys
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
import urllib3
urllib3.disable_warnings()
# 排除明显不是新闻内容的链接
EXCLUDED_WORDS = (
    "登录", "注册", "搜索", "首页", "关于我们",
    "联系我们", "隐私政策", "用户协议", "退出",
    "login", "register", "search", "logout",
)

# 尝试从页面文本中识别日期
DATE_PATTERN = re.compile(
    r"(20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?)"
)


def extract_date(text: str) -> str:
    """从文本中提取常见格式的日期。"""
    match = DATE_PATTERN.search(text)

    if not match:
        return ""

    return match.group(1)


def collect_news_list(url: str) -> list[dict]:
    """
    从新闻列表网页中提取候选新闻链接。

    注意：这是通用版本，不保证适配所有网站。
    网站专属 CSS 选择器后续可以单独配置。
    """

    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("请输入有效的 HTTP 或 HTTPS 网页地址")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        )
    }

    response = requests.get(
        url,
        headers=headers,
        timeout=30,
        verify=False
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    results = []
    seen_urls = set()

    # 优先遍历文章区域中的链接，再处理普通链接
    anchors = soup.select(
        "article a[href], .news-list a[href], "
        ".news-list li a[href], .article-list a[href]"
    )

    if not anchors:
        anchors = soup.select("a[href]")

    for anchor in anchors:
        title = anchor.get_text(" ", strip=True)
        href = anchor.get("href", "").strip()

        if not title or not href:
            continue

        if len(title) < 5:
            continue

        if any(word in title.lower() for word in EXCLUDED_WORDS):
            continue

        if href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue

        article_url = urljoin(response.url, href)
        article_parsed = urlparse(article_url)

        # 暂时只采集当前网站域名下的链接
        if article_parsed.netloc != urlparse(response.url).netloc:
            continue

        if article_parsed.scheme not in ("http", "https"):
            continue

        # 去除重复链接
        if article_url in seen_urls:
            continue

        seen_urls.add(article_url)

        # 从链接所在区域尝试提取发布日期
        parent = anchor.find_parent("li")
        if parent is None:
            parent = anchor.find_parent("article")
        if parent is None:
            parent = anchor.parent

        surrounding_text = (
            parent.get_text(" ", strip=True)
            if parent
            else title
        )

        results.append({
            "title": title,
            "url": article_url,
            "publish_date": extract_date(surrounding_text),
            "source_url": response.url,
            "collected_at": datetime.now(
                timezone.utc
            ).isoformat(),
        })

    return results


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(
            "使用方法：python collectors/news_list_collector.py "
            '"https://网站的新闻列表页地址"'
        )
        sys.exit(1)

    target_url = sys.argv[1]

    try:
        news_list = collect_news_list(target_url)

        print(f"候选新闻链接数量：{len(news_list)}")

        for index, news in enumerate(news_list[:10], start=1):
            print(f"\n第 {index} 条")
            print("标题：", news["title"])
            print("链接：", news["url"])
            print("发布日期：", news["publish_date"] or "未识别")

        if not news_list:
            print(
                "没有找到候选新闻链接。"
                "网站可能使用动态加载，或需要专属页面解析规则。"
            )

    except requests.RequestException as exc:
        print("网页请求失败：", exc)

    except ValueError as exc:
        print("输入错误：", exc)