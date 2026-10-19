"""OICA 统计数据:Playwright 渲染官网统计页,直接解析表格数值(不经过 LLM,保证数值与原表一致)。

用法:
    python collectors/oica_stats_collector.py --list-rows    # 打印识别到的区块与行名,核对 TARGET_ROWS
    python collectors/oica_stats_collector.py                # 渲染两个统计页并写入指标库

依赖: pip install playwright  且已执行 python -m playwright install chromium
"""
import argparse
import os
import re
import sys
from datetime import datetime

from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from processors.news_processor import normalize_value
from processors.store import load_metrics, save_metrics, metric_key

# ========== 配置 ==========
START_PAGES = [
    "https://www.oica.net/production-statistics/",
    "https://www.oica.net/sales-statistics/",
]


TARGET_ROWS = {
    "CHINA": "中国", "USA": "美国", "JAPAN": "日本", "GERMANY": "德国",
    "INDIA": "印度", "SOUTH KOREA": "韩国", "EUROPE": "欧洲",
    "TOTAL": "全球", "WORLD": "全球",
}
TYPE_CN = {"ALL VEHICLES": "汽车", "PASSENGER CARS": "乘用车", "COMMERCIAL VEHICLES": "商用车"}
DIR_CN = {"PRODUCTION": "产量", "SALES": "销量"}

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
}


def fetch_browser(url, retries=2, want_tables=3):
    """Playwright 渲染页面(OICA 表格是 JS 动态加载,requests 拿不到 <table>)。

    两个坑:
      1) 不能用 networkidle —— 页面有统计/长连接脚本,网络永不静默,必超时。
      2) 三个 tab 的表格是懒加载的,只等固定秒数常常只渲染出 1 个,
         必须轮询到 <table> 数量达标(最多再等 30s),否则销量/产量会缺一大半。
    """
    from playwright.sync_api import sync_playwright
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(user_agent=HEADERS["User-Agent"])
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                try:
                    page.wait_for_selector("table", timeout=30000)
                except Exception:
                    pass                      # 没有表格也让上层解析,由调用方判空

                best, best_n = page.content(), 0
                for _ in range(10):           # 轮询:最多再等 ~30s
                    page.wait_for_timeout(2000)
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(1000)
                    cur = page.content()
                    n = cur.count("<table")
                    if n > best_n:
                        best, best_n = cur, n
                    if best_n >= want_tables:
                        break
                print(f"  渲染完成: 表格 {best_n} 个")
                browser.close()
            if best_n:
                return best
            last_err = RuntimeError("页面未渲染出任何表格")
        except Exception as e:
            last_err = e
            print(f"  渲染第 {attempt} 次失败: {e}")
    raise last_err


# ---------- 解析 ----------
SECTION_RE = re.compile(
    r"(World Motor Vehicle|Passenger Cars|Commercial Vehicles?)\s*[-–]\s*(Sales|Production)"
    r"\s*[-–]\s*\(?([12][09]\d{2})(?:\s*(Q[1-4]))?", re.I)


def _type_cn(cat: str, col: str) -> str:
    c = col.upper()
    if cat.startswith("PASSENGER") or "PASSENGER" in c:
        return "乘用车"
    if cat.startswith("COMMERCIAL") or "COMMERCIAL" in c:
        return "商用车"
    if "CAR" in c:          # Production 表的 "Cars" 列
        return "乘用车"
    return "汽车"            # Sales 单列表

def parse_rendered_page(html, list_rows=False):
    """解析渲染后的统计页:每个「年份标题 + 表格」区块抽 TARGET_ROWS 国家的数值。"""
    soup = BeautifulSoup(html, "html.parser")
    metrics, seen = [], set()
    for h in soup.find_all(["h2", "h3"]):
        title = h.get_text(" ", strip=True).replace("\xa0", " ")
        m = SECTION_RE.search(title)
        if not m:
            continue
        cat, direction = m.group(1).upper(), m.group(2).upper()
        period = m.group(3) + (m.group(4) or "").upper()   # 整年="2025";季度页="2026Q1"
        tbl = h.find_next("table")
        rows = tbl.find_all("tr") if tbl else []
        if len(rows) < 2:
            continue
        header = [c.get_text(strip=True) for c in rows[0].find_all(["td", "th"])]
        for tr in rows[1:]:
            cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) < 2:
                continue
            base = re.split(r"[,(]", cells[0])[0].strip().upper()
            if list_rows:
                print("   行名:", title, "|", cells[0])
            if base not in TARGET_ROWS or (base, cat, direction, period) in seen:
                continue
            seen.add((base, cat, direction, period))
            cn = TARGET_ROWS[base]
            for col, val in zip(header[1:], cells[1:]):
                digits = val.replace(",", "").replace(" ", "")
                if not digits.isdigit():     # 过滤 "-1" 等占位符
                    continue
                v = int(digits)
                norm, base_unit, recognized = normalize_value(v, "辆")
                metrics.append({
                    "name": f"{cn}{_type_cn(cat, col)}{DIR_CN[direction]}",
                    "period": period, "value": v, "unit": "辆",
                    "value_normalized": norm, "base_unit": base_unit,
                    "unit_recognized": recognized, "source": "OICA",
                    "note": title[:60],
                })
    return metrics


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-rows", action="store_true", help="打印识别到的区块与行名")
    args = ap.parse_args()

    store = load_metrics()
    now = datetime.now().isoformat()
    total = 0
    for url in START_PAGES:
        print("渲染:", url)
        try:
            html = fetch_browser(url)
        except Exception as e:
            print("  渲染失败:", e)
            continue
        ms = parse_rendered_page(html, args.list_rows)
        print(f"  提取指标 {len(ms)} 条")
        if args.list_rows:
            continue
        for m in ms:
            m["url"], m["collected_at"] = url, now
            store[metric_key(m)] = m   # 同名同期以最新为准(OICA 会修订)
        total += len(ms)

    if not args.list_rows:
        save_metrics(store)
        print(f"完成:本次写入 {total} 条,指标库共 {len(store)} 条")
        if total == 0:
            print("没有提取到指标:先 --list-rows 看区块标题/行名。")


if __name__ == "__main__":
    main()

