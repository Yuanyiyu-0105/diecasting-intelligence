"""微信公众号采集(通过 RSS 订阅源)。

公众号没有公开接口,常见做法是用 RSS 桥接服务(如自建的 WeWe RSS、wechat2rss、RSSHub 等)
把公众号转成 RSS 地址,本脚本只负责读取这些地址。桥接服务的部署与账号授权由你自行完成。

配置: config/wechat_feeds.json
    [{"name": "压铸周刊", "url": "http://你的RSS服务/feed/xxx", "type": "news"}, ...]
    type=news : 新闻类,按压铸关键词预筛,命中才入库
    type=data : 销量/装机量数据类,标题含数据类关键词才入库

依赖: pip install feedparser
"""
import json
import os
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from processors.news_processor import extract_keywords, PREFILTER_MIN_SCORE
from processors.store import load_store, save_store, make_item, upsert

CONFIG_PATH = "config/wechat_feeds.json"
MAX_ENTRIES_PER_FEED = 20
REQUEST_INTERVAL = 2
DATA_TITLE_WORDS = ("销量", "产销", "零售", "批发", "出口", "装机", "产量", "月报",
                    "渗透率", "座舱", "数据", "排行")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def html_to_text(html):
    soup = BeautifulSoup(html or "", "html.parser")
    image = ""
    img = soup.find("img")
    if img:
        image = img.get("data-src") or img.get("src") or ""
    text = soup.get_text("\n", strip=True)
    lines, seen = [], set()
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if len(line) < 4 or line in seen:
            continue
        seen.add(line)
        lines.append(line)
    return "\n".join(lines), image


def fetch_wechat_page(url):
    """RSS 里正文太短时,尝试直接抓公众号文章页(可能被风控,失败则放弃)。"""
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        body = soup.find(id="js_content")
        if body:
            return html_to_text(str(body))
    except Exception:
        pass
    return "", ""


def main():
    try:
        import feedparser
    except ImportError:
        print("缺少依赖,请先执行: pip install feedparser")
        return

    if not os.path.exists(CONFIG_PATH):
        print(f"未找到 {CONFIG_PATH},跳过公众号采集")
        return
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        feeds = [x for x in json.load(f) if x.get("url", "").startswith("http")]
    if not feeds:
        print("config/wechat_feeds.json 里还没有填写有效的 RSS 地址,跳过公众号采集")
        return

    store = load_store()
    added = 0
    for feed in feeds:
        name, ftype = feed["name"], feed.get("type", "news")
        print(f"== {name} ({ftype})")
        parsed = feedparser.parse(feed["url"])
        if parsed.bozo and not parsed.entries:
            print("  读取失败:", getattr(parsed, "bozo_exception", "未知错误"))
            continue

        for e in parsed.entries[:MAX_ENTRIES_PER_FEED]:
            url, title = e.get("link", ""), (e.get("title") or "").strip()
            if not url or not title:
                continue
            if store.get(url, {}).get("status") == "ok":
                continue

            if ftype == "data" and not any(w in title for w in DATA_TITLE_WORDS):
                continue

            raw = ""
            if e.get("content"):
                raw = e["content"][0].get("value", "")
            raw = raw or e.get("summary", "")
            text, image = html_to_text(raw)
            if len(text) < 300 and "mp.weixin.qq.com" in url:
                t2, i2 = fetch_wechat_page(url)
                if len(t2) > len(text):
                    text, image = t2, i2 or image
                time.sleep(REQUEST_INTERVAL)

            score, hits = 0, None
            if ftype == "news":
                hits, score = extract_keywords(f"{title}\n{text}")
                if score < PREFILTER_MIN_SCORE:
                    continue

            ok = len(text) > 150
            upsert(store, make_item(
                title=title, url=url, source=name, media=name,
                source_type="data" if ftype == "data" else "news",
                content=text[:8000], status="ok" if ok else "short",
                error="" if ok else "RSS 正文过短", image_url=image,
                publish_date=(e.get("published") or "")[:10],
                prefilter_pass=True, prefilter_score=score, keywords=hits,
            ))
            added += 1
        save_store(store)

    print(f"完成:本次新增 {added} 条")


if __name__ == "__main__":
    main()
