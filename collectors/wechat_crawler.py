"""微信公众号采集（任务书附录C：wechat_crawler.py，Playwright 路线）。

背景：
    原 RSS 桥接方案（wechat_rss_collector.py + WeWe RSS）因上游中转服务
    weread.111965.xyz 永久关停（2026 年起持续 502，官方仓库已归档）而不可用。
    改用任务书规定的 Playwright 直接采集搜狗微信（weixin.sogou.com）。

重要事实（2026-10-19 实测）：
    搜狗的「搜公众号」接口（type=1）已下线——任何账号名都返回
    "暂无相关官方认证订阅号"。存活的是「搜文章」接口（type=2），
    故本采集器按压铸行业关键词搜索全网公众号文章，每条结果自带公众号名，
    与项目"关键词预筛"口径天然一致。

流程：关键词搜索 → 文章卡片(标题/公众号/日期/跳转链接) → 跳转 mp.weixin.qq.com
抓正文 → 关键词预筛（与 RSS 采集器同一套）→ 入库。去重键为跳转后的真实
mp.weixin.qq.com URL，与 RSS 采集器兼容。

关键词表：config/wechat_queries.json（不存在时用内置默认值）。

前置：撞到验证码时先运行 collectors/wechat_login.py 人工过一次
（会话保存在 data/wechat_profile/）。pipeline 中本步骤遇验证码会跳过并记日志，
不影响其他步骤。

CLI:
    python collectors/wechat_crawler.py            # 全部关键词，无头
    python collectors/wechat_crawler.py --limit 1  # 只跑前 1 个词（冒烟测试）
    python collectors/wechat_crawler.py --headed   # 带界面（调试用）
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from urllib.parse import quote, urljoin

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from processors.news_processor import extract_keywords, PREFILTER_MIN_SCORE
from processors.store import load_store, save_store, make_item, upsert

QUERIES_PATH = "config/wechat_queries.json"
PROFILE_DIR = os.path.join("data", "wechat_profile")
DEBUG_DIR = "logs"

# 行业关键词：覆盖压铸本体 + 一体化压铸 + 下游汽车产销数据
DEFAULT_QUERIES = ["压铸", "一体化压铸", "压铸机", "汽车产销", "乘用车销量"]

MAX_RESULTS_PER_QUERY = 10
PAGE_TIMEOUT = 30000
REQUEST_INTERVAL = 2.5            # 每篇正文间隔
QUERY_INTERVAL = 6.0              # 关键词间隔，压低触发验证码的概率

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


# ---------------- 工具 ----------------

def is_captcha(page):
    """搜狗反爬页判定：URL 进 antispider 或页面出现验证码提示。"""
    try:
        if "antispider" in page.url:
            return True
        body = page.locator("body").inner_text()[:2000]
        return ("请输入验证码" in body) or ("用户您好，我们的系统检测到您" in body)
    except Exception:
        return False


def dump_debug(page, tag):
    os.makedirs(DEBUG_DIR, exist_ok=True)
    ts = datetime.now().strftime("%H%M%S")
    safe = re.sub(r"[^\w一-鿿]+", "_", tag)
    try:
        page.screenshot(path=os.path.join(DEBUG_DIR, f"wechat_{safe}_{ts}.png"))
        with open(os.path.join(DEBUG_DIR, f"wechat_{safe}_{ts}.html"), "w",
                  encoding="utf-8") as f:
            f.write(page.content())
    except Exception:
        pass


def canonical_mp_url(html, fallback):
    """把落地页的临时 URL（含每次变化的 timestamp/signature）换成稳定规范 URL。

    搜狗跳转落地形如 mp.weixin.qq.com/s?src=3&timestamp=...&signature=...，
    其中的 timestamp 每次访问都不同，直接拿来当去重键会导致同一篇文章
    每天重复入库。改取页面里的 var biz / mid / idx 拼规范链接。
    """
    biz = re.search(r'var\s+biz\s*=\s*["\']([^"\']+)', html or "")
    mid = re.search(r'var\s+mid\s*=\s*["\']?(\d+)', html or "")
    if not (biz and mid):
        return fallback
    idx = re.search(r'var\s+idx\s*=\s*["\']?(\d+)', html or "")
    return (f"https://mp.weixin.qq.com/s?__biz={quote(biz.group(1))}"
            f"&mid={mid.group(1)}&idx={idx.group(1) if idx else '1'}")


def parse_sp(text):
    """解析卡片 .s-p 文本 → (公众号名, 日期 YYYY-MM-DD)。

    搜狗日期有两种写法：
      老文章：绝对日期 "公众号名2023-10-11"（月/日可能不补零）
      近期文：相对时间 "公众号名3小时前" / "2天前" / "昨天"
    """
    from datetime import timedelta
    text = re.sub(r"\s+", "", text or "")
    now = datetime.now()

    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})$", text)
    if m:
        y, mo, d = m.groups()
        return text[:m.start()], f"{y}-{int(mo):02d}-{int(d):02d}"

    m = re.search(r"(\d+)(分钟|小时|天|周)前$", text)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = {"分钟": timedelta(minutes=n), "小时": timedelta(hours=n),
                 "天": timedelta(days=n), "周": timedelta(weeks=n)}[unit]
        return text[:m.start()], (now - delta).strftime("%Y-%m-%d")

    if text.endswith("昨天"):
        return text[:-2], (now - timedelta(days=1)).strftime("%Y-%m-%d")
    if text.endswith("前天"):
        return text[:-2], (now - timedelta(days=2)).strftime("%Y-%m-%d")
    return text, ""


def load_queries():
    if os.path.exists(QUERIES_PATH):
        try:
            with open(QUERIES_PATH, "r", encoding="utf-8") as f:
                qs = [q for q in json.load(f) if isinstance(q, str) and q.strip()]
            if qs:
                return qs
        except Exception:
            pass
    return DEFAULT_QUERIES


# ---------------- 单关键词采集 ----------------

def collect_query(context, store, query):
    """返回 (added, need_login)。need_login=True 表示撞到验证码。"""
    page = context.new_page()
    try:
        # 注：搜狗时间过滤参数(timestamp/tsn)实测会把结果页刷成空壳，不支持。
        # 增量靠「真实 mp 链接去重」+ 卡片日期 publish_date 保证。
        page.goto(
            f"https://weixin.sogou.com/weixin?type=2&query={quote(query)}&ie=utf8",
            timeout=PAGE_TIMEOUT, wait_until="domcontentloaded")
        time.sleep(1)
        if is_captcha(page):
            print("  [验证码] 搜索页被拦，需运行 wechat_login.py 过验证码")
            return 0, True

        items = page.locator(".news-list li")
        n = min(items.count(), MAX_RESULTS_PER_QUERY)
        if n == 0:
            print("  无结果或结构变更，已存调试快照")
            dump_debug(page, f"search_{query}")
            return 0, False
        print(f"  结果 {n} 条")

        added = 0
        for i in range(n):
            li = items.nth(i)
            try:
                a = li.locator("h3 a")
                if a.count() == 0:
                    continue
                a = a.first
                title = re.sub(r"\s+", " ", (a.inner_text() or "")).strip()
                href = a.get_attribute("href") or ""
                if not title or not href:
                    continue

                account, pub = "", ""
                sp = li.locator(".s-p")
                if sp.count():
                    account, pub = parse_sp(sp.first.inner_text())

                link = urljoin("https://weixin.sogou.com/", href)

                # 抓正文：搜狗链接是 JS 跳转页，必须等跳到真实 mp 地址再取值，
                # 否则去重键会是每天变化的临时链接（产生重复条目）
                art = context.new_page()
                try:
                    art.goto(link, timeout=PAGE_TIMEOUT,
                             wait_until="domcontentloaded")
                    try:
                        art.wait_for_url("**://mp.weixin.qq.com/**",
                                         timeout=15000)
                    except Exception:
                        print(f"    - 跳转未达 mp，跳过: {title[:30]}")
                        continue
                    real_url = canonical_mp_url(art.content(), art.url)
                    if store.get(real_url, {}).get("status") == "ok":
                        continue

                    text = ""
                    try:
                        art.wait_for_selector("#js_content", timeout=10000)
                        text = art.locator("#js_content").first.inner_text()
                    except Exception:
                        pass
                    text = re.sub(r"\n{3,}", "\n\n", (text or "")).strip()
                    if "环境异常" in text[:200] or "操作频繁" in text[:200]:
                        print(f"    - 微信风控页，跳过: {title[:30]}")
                        continue

                    hits, score = extract_keywords(f"{title}\n{text}")
                    if score < PREFILTER_MIN_SCORE:
                        continue

                    ok = len(text) > 150
                    upsert(store, make_item(
                        title=title, url=real_url,
                        source=account or "微信公众号",
                        media=account or "微信公众号",
                        source_type="news",
                        content=text[:8000], status="ok" if ok else "short",
                        error="" if ok else "正文抓取过短",
                        publish_date=pub,
                        prefilter_pass=True, prefilter_score=score, keywords=hits,
                        extra={"channel": "wechat_sogou", "query": query},
                    ))
                    added += 1
                    print(f"    + [{account or '公众号'}] {title[:36]}")
                finally:
                    art.close()
                time.sleep(REQUEST_INTERVAL)
            except Exception as e:
                print(f"    ! 单篇失败: {type(e).__name__} {e}")
                continue
        return added, False
    except Exception as e:
        print(f"  ! 关键词失败: {type(e).__name__} {e}")
        dump_debug(page, f"query_{query}")
        return 0, False
    finally:
        try:
            page.close()
        except Exception:
            pass


# ---------------- 主入口 ----------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个关键词")
    ap.add_argument("--headed", action="store_true", help="带界面运行（调试用）")
    args = ap.parse_args()

    queries = load_queries()
    if args.limit:
        queries = queries[:args.limit]

    headless = not args.headed and os.environ.get("WECHAT_HEADLESS", "1") != "0"

    from playwright.sync_api import sync_playwright
    store = load_store()
    total = 0
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE_DIR,
            headless=headless,
            viewport={"width": 1280, "height": 860},
            user_agent=UA,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            for q in queries:
                print(f"== 搜索: {q}")
                added, need_login = collect_query(ctx, store, q)
                total += added
                save_store(store)
                if need_login:
                    print(">> 搜狗要求验证码：请先运行 "
                          "collectors/wechat_login.py 人工验证，再跑本采集器")
                    break
                time.sleep(QUERY_INTERVAL)
        finally:
            ctx.close()

    print(f"完成:本次新增 {total} 条")


if __name__ == "__main__":
    main()
