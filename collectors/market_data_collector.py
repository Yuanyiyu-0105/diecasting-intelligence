"""IDC(全球PC、AI眼镜)与 BCN(日本PC)的销量/出货量资讯采集。

流程:列表页 -> 按标题关键词筛出销量类文章 -> 抓正文 -> 写入统一存储,
数值由 LLM 层抽取(统一指标名、统一单位)。这些来源属于「数据类信源」,不经过关键词预筛。

注意:BCN 的资讯与排行榜文章主要发布在 bcnretail.com(BCN+R),bcn.co.jp 是公司门户。
requests 被拦截(403)时自动改用 Playwright 浏览器重试一次。
"""
import os
import re
import sys
import time
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
import urllib3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from processors.store import (load_store, save_store, make_item, upsert,
                              load_metrics, save_metrics, metric_key)
from processors.metric_extract import extract_market_metrics

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ========== 配置 ==========
PROXY = ""
REQUEST_INTERVAL = 2
CONNECT_TIMEOUT = 10
READ_TIMEOUT = 30

SOURCES = [
    {
        # IDC 的列表页只展示最新约 10 篇,且 ROC-friendly 的 tracker PR(智能眼镜/亚太PC/日本PC)
        # 不全在里面。官方 RSS(2672.do) 是滚动窗口,每季度 tracker PR 都会经过它,
        # 所以 RSS 作主力、列表页作补充。
        "name": "IDC",
        "rss_url": "https://www.idc.com/rss/2672.do",
        "list_urls": [
            "https://www.idc.com/about/press-releases/",       # 原 /resource-center/ 已 301 到此
            "https://www.idc.com/jp/about/press-releases/",    # IDC Japan 日语发布页
        ],
        # slug 可能是 URL 编码的中文/日文(如 .../idc%EF%BC%9A...),故用 [^/]+ 而非 [a-z0-9\-_]+
        "link_re": r"/(?:resource-center|about)/press-releases/[^/]+/?$",
        # 中文/日文/繁体稿也要命中:智能眼镜出货量、亚太PC、パソコン出荷
        "title_keywords": ["pc", "personal computing", "glasses", "ar/vr", "ar-vr", "xr",
                           "eyewear", "wearable", "smartring", "handheld",
                           "智能眼镜", "出货量", "腕戴", "可穿戴", "亚太",
                           "パソコン", "眼镜", "眼鏡"],
        "max_items": 12,
    },
    {
        # BCN 的 PC 内容不在 /research/ 和 /market/(那两个是家电·生活类,实测 PC 命中 0),
        # 而在 contents_type=81「スマホ・PC」分类,以及 contents_type=90「売れてる分析」。
        "name": "BCN",
        "list_urls": [
            "https://www.bcnretail.com/ranking/research/contents_type=90",  # 売れてる分析(含销量)
            "https://www.bcnretail.com/news/contents_type=81",              # スマホ・PC 分类
            "https://www.bcnretail.com/ranking/",                           # 売れてるもの总榜
        ],
        # 必须含 news:文章详情页路径是 /news/detail/ 和 /ranking/research/detail/
        "link_re": r"/(?:research|market|news)/detail/(\d{8})_\d+\.html",
        "title_keywords": ["パソコン", "pc", "ノート", "デスクトップ", "販売台数", "実売",
                           "タブレット", "chromebook", "windows"],
        "max_items": 12,
    },
]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9,ja;q=0.8,zh-CN;q=0.7"}
DATE_RE = re.compile(r"((?:January|February|March|April|May|June|July|August|September|"
                     r"October|November|December)\s+\d{1,2},\s+20\d{2}|20\d{2}[./年]\d{1,2}[./月]\d{1,2})")


def build_session():
    s = requests.Session()
    s.trust_env = True
    if PROXY:
        s.proxies = {"http": PROXY, "https": PROXY}
    return s


def fetch_browser(url):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(ignore_https_errors=True, user_agent=UA)
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
        html = page.content()
        browser.close()
    return html


def fetch_html(session, url):
    """先用 requests,失败或被拦截再用浏览器。返回 (html, error)。"""
    try:
        r = session.get(url, headers=HEADERS, verify=False,
                        timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
        if r.status_code == 200 and len(r.text) > 2000:
            if not r.encoding or r.encoding.lower() == "iso-8859-1":
                r.encoding = r.apparent_encoding
            return r.text, ""
        err = f"HTTP {r.status_code}"
    except Exception as e:
        err = str(e)[:120]
    try:
        print("   requests 失败,改用浏览器:", err)
        return fetch_browser(url), ""
    except Exception as e:
        return "", f"{err}; 浏览器也失败: {str(e)[:120]}"


def clean_tracking_url(u):
    """去掉 utm_* 跟踪参数,并把 my.idc.com 统一成 www.idc.com。"""
    p = urlparse(u)
    q = [(k, v) for k, v in parse_qsl(p.query) if not k.startswith("utm_")]
    host = p.netloc.replace("my.idc.com", "www.idc.com")
    return urlunparse((p.scheme, host, p.path, p.params, urlencode(q), ""))


def fetch_rss(session, url):
    """RSS 里的条目 -> [(title, url, date)]。标题自带语言原文,直接交给标题关键词过滤。

    feedparser 直接 parse URL 会卡在 302(www -> my.idc.com),所以用 requests 抓内容再喂它。
    """
    html, err = fetch_html(session, url)
    if not html:
        print("  RSS 获取失败:", err)
        return []
    try:
        import feedparser
        feed = feedparser.parse(html)
    except ImportError:
        print("  未安装 feedparser,跳过 RSS")
        return []

    out = []
    for e in feed.entries:
        title = re.sub(r"\s+", " ", e.get("title", "")).strip()
        link = clean_tracking_url(e.get("link", ""))
        if not link:
            continue
        date = ""
        for key in ("published_parsed", "updated_parsed"):
            tp = e.get(key)
            if tp:
                date = time.strftime("%Y-%m-%d", tp)
                break
        out.append((title, link, date))
    return out


def find_links(html, base_url, source):
    """从列表页提取候选文章 [(title, url, publish_date)],按页面顺序去重。"""
    soup = BeautifulSoup(html, "html.parser")
    pat = re.compile(source["link_re"])
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        url = urljoin(base_url, a["href"]).split("#")[0]
        path = urlparse(url).path
        m = pat.search(path)
        if not m or url in seen:
            continue
        seen.add(url)
        slug = path.rstrip("/").rsplit("/", 1)[-1]
        title = a.get_text(" ", strip=True) or slug
        date = ""
        if m.groups():                       # BCN: URL 中带 8 位日期
            d = m.group(1)
            date = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        hay = f"{title} {slug}".lower()
        if any(k.lower() in hay for k in source["title_keywords"]):
            out.append((title, url, date))
    return out


def extract_article(html):
    """返回 (标题, 正文, 配图URL, 页面日期)。保留短行(排行榜/表格行很短),只去重和去噪音标签。"""
    soup = BeautifulSoup(html, "html.parser")
    og_img = soup.find("meta", attrs={"property": "og:image"})
    image = (og_img.get("content") or "").strip() if og_img else ""
    og_title = soup.find("meta", attrs={"property": "og:title"})
    h1 = soup.find("h1")
    title = ((og_title.get("content") if og_title else "")
             or (h1.get_text(" ", strip=True) if h1 else "")
             or (soup.title.get_text(strip=True) if soup.title else ""))

    for tag in soup(["script", "style", "noscript", "nav", "header", "footer",
                     "aside", "form", "iframe", "svg"]):
        tag.decompose()
    container = soup.find("article") or soup.find("main") or soup.body or soup
    seen, lines = set(), []
    for line in container.get_text("\n", strip=True).splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if len(line) < 6 or line in seen:
            continue
        seen.add(line)
        lines.append(line)
    text = "\n".join(lines)
    m = DATE_RE.search(text[:3000])
    return title.strip(), text[:8000], image, (m.group(1) if m else "")


def run():
    session = build_session()
    store = load_store()
    metrics = load_metrics()
    total_new = 0
    metric_added = 0

    for src in SOURCES:
        print(f"== {src['name']}")
        candidates = []

        if src.get("rss_url"):
            rss_items = fetch_rss(session, src["rss_url"])
            print(f"  RSS {src['rss_url']} -> {len(rss_items)} 条")
            hay_ok = [(t, u, d) for t, u, d in rss_items
                      if any(k.lower() in (t + " " + u).lower() for k in src["title_keywords"])]
            print(f"    命中标题关键词 {len(hay_ok)} 篇")
            candidates.extend(hay_ok)
            time.sleep(REQUEST_INTERVAL)

        for list_url in src["list_urls"]:
            html, err = fetch_html(session, list_url)
            if not html:
                print("  列表页失败:", list_url, err)
                continue
            found = find_links(html, list_url, src)
            print(f"  {list_url} -> 命中标题关键词的文章 {len(found)} 篇")
            candidates.extend(found)
            time.sleep(REQUEST_INTERVAL)

        # 按 URL 去重(RSS 与列表页会重)
        dedup = {}
        for c in candidates:
            dedup.setdefault(c[1], c)
        candidates = list(dedup.values())

        new = [c for c in candidates
               if not (store.get(c[1]) and store[c[1]].get("status") == "ok")]
        for title, url, date in new[: src["max_items"]]:
            print("  抓取:", url)
            html, err = fetch_html(session, url)
            time.sleep(REQUEST_INTERVAL)
            if not html:
                upsert(store, make_item(title, url, src["name"], src["name"], "data",
                                        status="failed", error=err, publish_date=date))
                continue
            real_title, text, image, page_date = extract_article(html)
            ok = len(text) > 300
            final_title = real_title or title
            upsert(store, make_item(
                title=final_title, url=url, source=src["name"], media=src["name"],
                source_type="data", content=text, status="ok" if ok else "short",
                error="" if ok else "正文过短,可能为动态加载或需要登录",
                image_url=image, publish_date=date or page_date,
                prefilter_pass=True,   # 数据类信源:按来源保留,相关性交给 LLM 判断
            ))
            total_new += 1
            # 出货量/销量数值直接取自原文,不走 LLM
            for m in extract_market_metrics(text, src["name"], url,
                                            date or page_date, final_title):
                m["collected_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                metrics[metric_key(m)] = m
                metric_added += 1
        save_store(store)
        save_metrics(metrics)

    print(f"完成:本次新增 {total_new} 条;本次写入指标 {metric_added} 条,"
          f"指标库共 {len(metrics)} 条")


if __name__ == "__main__":
    run()
