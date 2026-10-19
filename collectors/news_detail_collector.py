import os
import sys
import json
import time
from datetime import datetime

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
import urllib3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from processors.news_processor import (
    clean_url, identify_media, clean_text, extract_keywords,
    PAYWALL_MEDIA, PREFILTER_MIN_SCORE,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ========== 配置 ==========
PROXY = ""  # 需要代理时填,如 "http://127.0.0.1:7890";留空用系统代理
CONNECT_TIMEOUT = 10
READ_TIMEOUT = 30
MAX_ARTICLES = 30
REQUEST_INTERVAL = 2
MIN_VALID_LEN = 300
OUT_PATH = "data/clean/industry_news.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
}


def load_news_list():
    """读取 OICA 新闻列表。

    该文件由 collectors/oica_news_collector.py 产出。若列表还没生成
    (比如该步骤失败),这里直接退出而不是抛异常,避免拖垮整条流水线。
    """
    path = "data/raw/oica_news.json"
    if not os.path.exists(path):
        print(f"未找到 {path},跳过正文采集(请先跑 OICA 新闻列表步骤)")
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_existing():
    if os.path.exists(OUT_PATH):
        with open(OUT_PATH, "r", encoding="utf-8") as f:
            return {x["url"]: x for x in json.load(f)}
    return {}


def build_session():
    session = requests.Session()
    session.trust_env = True
    if PROXY:
        session.proxies = {"http": PROXY, "https": PROXY}
    retry = Retry(total=2, backoff_factor=1,
                  status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=["GET"])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def parse_html(html):
    """返回 (正文文本, 配图URL)"""
    soup = BeautifulSoup(html, "html.parser")

    og = soup.find("meta", attrs={"property": "og:image"})
    image_url = (og.get("content") or "").strip() if og else ""

    for tag in soup(["script", "style", "noscript", "nav", "header",
                     "footer", "aside", "form", "iframe", "svg"]):
        tag.decompose()

    container = soup.find("article") or soup.find("main") or soup.body or soup
    paragraphs = [p.get_text(" ", strip=True) for p in container.find_all("p")]
    paragraphs = [p for p in paragraphs if len(p) > 30]
    text = "\n".join(paragraphs) if paragraphs else container.get_text("\n", strip=True)
    return text, image_url


def fetch_detail(session, url):
    print("正在访问:", url)
    out = {"text": "", "image_url": "", "status": "failed", "error": ""}
    try:
        r = session.get(url, headers=HEADERS, verify=False,
                        timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
        if r.status_code in (401, 403):
            out.update(status="blocked", error=f"HTTP {r.status_code}(付费墙或反爬)")
            return out
        if r.status_code != 200:
            out.update(error=f"HTTP {r.status_code}")
            return out

        if not r.encoding or r.encoding.lower() == "iso-8859-1":
            r.encoding = r.apparent_encoding

        out["text"], out["image_url"] = parse_html(r.text)
        out["status"] = "ok"
    except requests.exceptions.ConnectTimeout:
        print("失败: 连接超时(检查网络/代理)")
        out.update(status="timeout", error="connect timeout")
    except Exception as e:
        print("失败:", e)
        out.update(error=str(e))
    return out


def save_data(data):
    os.makedirs("data/clean", exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


if __name__ == "__main__":
    news_list = load_news_list()
    existing = load_existing()
    session = build_session()
    new_count = 0

    for news in news_list[:MAX_ARTICLES]:
        url = clean_url(news["url"])
        old = existing.get(url)

        # 已成功采集过的不再重复抓取
        if old and old.get("status") == "ok" and "prefilter_pass" in old:
            continue

        media = identify_media(url)
        res = fetch_detail(session, url)
        content = clean_text(res["text"])
        status, error = res["status"], res["error"]

        if status == "ok" and len(content) < MIN_VALID_LEN:
            status = "short"
            error = "付费墙或动态加载" if media in PAYWALL_MEDIA else "正文过短"

        # 正文没拿到时只用标题预筛;标题命中也放行给 LLM 判断
        hits, score = extract_keywords(f"{news['title']}\n{content}")
        now = datetime.now().isoformat()

        existing[url] = {
            "title": news["title"],
            "url": url,
            "source": news.get("source", ""),
            "media": media,
            "status": status,
            "error": error,
            "content": content,
            "image_url": res["image_url"],
            "keywords": hits,
            "prefilter_score": score,
            "prefilter_pass": score >= PREFILTER_MIN_SCORE,
            "first_seen": (old or {}).get("first_seen", now),
            "last_checked": now,
        }
        new_count += 1
        time.sleep(REQUEST_INTERVAL)

    save_data(list(existing.values()))

    items = list(existing.values())
    ok = sum(x["status"] == "ok" for x in items)
    passed = sum(x["prefilter_pass"] for x in items)
    print(f"本次新采集 {new_count} 条;库内共 {len(items)} 条,正文成功 {ok},预筛通过 {passed}")