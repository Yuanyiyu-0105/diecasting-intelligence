"""巨潮资讯网:压铸上市公司的定期报告(财报)与重要公告。

用法:
    python collectors/cninfo_collector.py            # 正常采集
    python collectors/cninfo_collector.py --check    # 只核对股票代码/orgId 和最新公告,不下载 PDF

依赖: pip install pdfplumber
"""
import io
import os
import re
import sys
import time
from datetime import datetime, timedelta

import requests
import urllib3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from processors.store import (load_store, save_store, make_item, upsert,
                              load_metrics, save_metrics, metric_key)
from processors.metric_extract import extract_financial_metrics

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ========== 配置 ==========
# 名称, 股票代码(请在 --check 时核对巨潮返回的简称是否一致)
COMPANIES = [
    ("文灿股份", "603348"),
    ("旭升集团", "603305"),
    ("爱柯迪", "600933"),
    ("广东鸿图", "002101"),
    ("星源卓镁", "301398"),
    ("宜安科技", "300328"),
]

PROXY = ""
REQUEST_INTERVAL = 1.5
PERIODIC_LOOKBACK_DAYS = 420     # 定期报告回溯天数(覆盖年报+最近季报)
ANNOUNCE_LOOKBACK_DAYS = 30      # 普通公告回溯天数
MAX_PERIODIC_PER_COMPANY = 4
MAX_PDF_DOWNLOADS = 30           # 单次运行最多下载的 PDF 数

QUERY_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
SEARCH_URL = "http://www.cninfo.com.cn/new/information/topSearch/query"
PDF_BASES = ["https://static.cninfo.com.cn/", "http://static.cninfo.com.cn/"]
# 年报 / 半年报 / 一季报 / 三季报
PERIODIC_CATEGORY = ("category_ndbg_szsh;category_bndbg_szsh;"
                     "category_yjdbg_szsh;category_sjdbg_szsh;")

# 与压铸业务相关的公告标题关键词(命中才保留,其余如股东大会通知等直接忽略)
ANN_KEYWORDS = ("合同", "投资", "扩产", "产能", "定点", "募集", "募投", "并购", "收购",
                "压铸", "订单", "业绩预告", "业绩快报", "项目", "合资", "股权", "增资",
                "产线", "基地", "工厂", "镁", "铝")

FIN_MARKERS = ("主要会计数据", "主要财务数据", "主要财务指标")


def build_session():
    s = requests.Session()
    s.trust_env = True
    if PROXY:
        s.proxies = {"http": PROXY, "https": PROXY}
    s.headers.update({
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
        "Referer": "http://www.cninfo.com.cn/new/disclosure",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
    })
    return s


def market_of(code):
    """返回 (column, plate)"""
    return ("sse", "sh") if code.startswith(("6", "9")) else ("szse", "sz")


def resolve_org(session, code):
    """股票代码 -> (orgId, 巨潮简称)。先查接口,失败再按规则推测。"""
    try:
        r = session.post(SEARCH_URL, data={"keyWord": code, "maxNum": 10},
                         timeout=20, verify=False)
        for it in r.json():
            if str(it.get("code")) == code:
                return it.get("orgId"), it.get("zwjc", "")
    except Exception as e:
        print("  orgId 查询失败:", e)
    prefix = "gssh0" if code.startswith("6") else "gssz0"
    print("  ⚠ 使用推测的 orgId:", prefix + code)
    return prefix + code, ""


def query(session, code, org_id, category, start, end, max_pages=3):
    column, plate = market_of(code)
    out = []
    for page in range(1, max_pages + 1):
        data = {
            "pageNum": page, "pageSize": 30, "column": column, "tabName": "fulltext",
            "plate": plate, "stock": f"{code},{org_id}", "searchkey": "", "secid": "",
            "category": category, "trade": "",
            "seDate": f"{start:%Y-%m-%d}~{end:%Y-%m-%d}",
            "sortName": "", "sortType": "", "isHLtitle": "true",
        }
        try:
            js = session.post(QUERY_URL, data=data, timeout=30, verify=False).json()
        except Exception as e:
            print("  公告查询失败:", e)
            break
        out.extend(js.get("announcements") or [])
        if not js.get("hasMore"):
            break
        time.sleep(REQUEST_INTERVAL)
    return out


def clean_title(t):
    return re.sub(r"<[^>]+>", "", t or "").strip()


def want_periodic(title):
    """年报/半年报只取摘要版(篇幅短,主要数据在前两页);季报取全文。"""
    if "英文" in title:
        return False
    if ("年度报告" in title) and "摘要" not in title:
        return False
    return any(k in title for k in ("年度报告", "季度报告"))


def want_announcement(title):
    return any(k in title for k in ANN_KEYWORDS)


def pdf_text(session, adjunct, max_pages):
    import pdfplumber
    content = None
    for base in PDF_BASES:
        try:
            r = session.get(base + adjunct, timeout=60, verify=False)
            if r.status_code == 200 and r.content[:4] == b"%PDF":
                content = r.content
                break
        except Exception:
            continue
    if content is None:
        return ""
    pages = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for p in pdf.pages[:max_pages]:
            pages.append(p.extract_text() or "")
    return "\n".join(pages)


def focus_financial(text, size=5500):
    """定位「主要会计数据」段落(后面要带数字才算,避开目录)。
    注意:财报表格行很短,不能用 clean_text 清洗,否则数字行会被当噪音删掉。"""
    for m in re.finditer("|".join(FIN_MARKERS), text):
        window = text[m.start(): m.start() + 600]
        if re.search(r"\d{1,3}(,\d{3}){2,}", window):
            return text[max(0, m.start() - 100): m.start() + size]
    return text[: size + 100]


def ms_to_date(ms):
    try:
        return datetime.fromtimestamp(int(ms) / 1000).strftime("%Y-%m-%d")
    except Exception:
        return ""


def run(check_only=False):
    session = build_session()
    store = {} if check_only else load_store()
    metrics = {} if check_only else load_metrics()
    today = datetime.now()
    downloads = 0
    added = 0
    metric_added = 0

    for name, code in COMPANIES:
        print(f"== {name} {code}")
        org_id, zwjc = resolve_org(session, code)
        print(f"  orgId={org_id} 巨潮简称={zwjc or '(未获取)'}")
        if zwjc and zwjc != name:
            print(f"  ⚠ 巨潮简称「{zwjc}」与配置「{name}」不一致,请核对股票代码")
        time.sleep(REQUEST_INTERVAL)

        periodic = query(session, code, org_id, PERIODIC_CATEGORY,
                         today - timedelta(days=PERIODIC_LOOKBACK_DAYS), today)
        periodic = [a for a in periodic if want_periodic(clean_title(a.get("announcementTitle")))]
        periodic.sort(key=lambda a: a.get("announcementTime") or 0, reverse=True)
        time.sleep(REQUEST_INTERVAL)

        anns = query(session, code, org_id, "",
                     today - timedelta(days=ANNOUNCE_LOOKBACK_DAYS), today)
        anns = [a for a in anns if want_announcement(clean_title(a.get("announcementTitle")))]
        print(f"  定期报告 {len(periodic)} 份(取最近 {MAX_PERIODIC_PER_COMPANY}),相关公告 {len(anns)} 条")

        if check_only:
            for a in (periodic[:2] + anns[:3]):
                print("   -", ms_to_date(a.get("announcementTime")), clean_title(a.get("announcementTitle")))
            continue

        jobs = [(a, "financial") for a in periodic[:MAX_PERIODIC_PER_COMPANY]]
        jobs += [(a, "announcement") for a in anns]

        for a, stype in jobs:
            adjunct = a.get("adjunctUrl") or ""
            if not adjunct:
                continue
            url = PDF_BASES[0] + adjunct
            old = store.get(url)
            if old and old.get("status") == "ok":
                continue
            if downloads >= MAX_PDF_DOWNLOADS:
                print("  达到单次下载上限,剩余留待下次")
                break

            title = f"{name}:{clean_title(a.get('announcementTitle'))}"
            print("  下载:", title)
            try:
                text = pdf_text(session, adjunct, 50 if stype == "financial" else 10)
            except Exception as e:
                text = ""
                print("   解析失败:", e)
            downloads += 1
            time.sleep(REQUEST_INTERVAL)

            if stype == "financial":
                content = focus_financial(text)
                # 数值直接解析自表格(不走 LLM),可与原表逐字核对
                for m in extract_financial_metrics(content, name, title, url):
                    m["collected_at"] = today.isoformat()
                    metrics[metric_key(m)] = m
                    metric_added += 1
            else:
                content = text
            ok = len(content.strip()) > 150
            upsert(store, make_item(
                title=title, url=url, source="cninfo", media="巨潮资讯网",
                source_type=stype, content=content,
                status="ok" if ok else "short",
                error="" if ok else "PDF 无文本层或下载失败",
                publish_date=ms_to_date(a.get("announcementTime")),
                prefilter_pass=True,
                extra={"company": name, "stock_code": code},
            ))
            added += 1
        save_store(store)
        save_metrics(metrics)

    if not check_only:
        print(f"完成:本次新增/更新 {added} 条;本次写入财报指标 {metric_added} 条,"
              f"指标库共 {len(metrics)} 条")


if __name__ == "__main__":
    run(check_only="--check" in sys.argv)
