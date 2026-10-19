"""统一数据存储:新闻/公告条目 与 数值指标。

所有采集器都往 data/clean/industry_news.json 追加条目(按 URL 去重),
数值型数据(如 OICA 产销量)写入 data/clean/metrics.json,报告直接读取绘图。
"""
import json
import os
from datetime import datetime

STORE_PATH = "data/clean/industry_news.json"
METRICS_PATH = "data/clean/metrics.json"


def _atomic_dump(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    os.replace(tmp, path)


# ---------- 新闻 / 公告条目 ----------
def load_store():
    if os.path.exists(STORE_PATH):
        with open(STORE_PATH, "r", encoding="utf-8") as f:
            return {x["url"]: x for x in json.load(f)}
    return {}


def save_store(store):
    _atomic_dump(STORE_PATH, list(store.values()))


def make_item(title, url, source, media, source_type, content="", status="ok",
              error="", image_url="", publish_date="", prefilter_pass=True,
              prefilter_score=0, keywords=None, extra=None):
    """生成与 news_detail_collector 同结构的条目,可直接被 llm_analyzer 处理。

    source_type: news(新闻) / data(统计机构数据) / financial(财报) / announcement(公告)
    """
    now = datetime.now().isoformat()
    item = {
        "title": title,
        "url": url,
        "source": source,
        "media": media,
        "source_type": source_type,
        "status": status,
        "error": error,
        "content": content,
        "image_url": image_url,
        "publish_date": publish_date,
        "keywords": keywords or {"core": {}, "downstream": {}},
        "prefilter_score": prefilter_score,
        "prefilter_pass": prefilter_pass,
        "first_seen": now,
        "last_checked": now,
    }
    if extra:
        item.update(extra)
    return item


def upsert(store, item):
    """已存在则保留 first_seen,其余字段更新。"""
    old = store.get(item["url"])
    if old:
        item["first_seen"] = old.get("first_seen", item["first_seen"])
    store[item["url"]] = item


# ---------- 数值指标 ----------
def load_metrics():
    if os.path.exists(METRICS_PATH):
        with open(METRICS_PATH, "r", encoding="utf-8") as f:
            return {metric_key(m): m for m in json.load(f)}
    return {}


def save_metrics(metrics):
    _atomic_dump(METRICS_PATH, list(metrics.values()))


def metric_key(m):
    return f"{m['name']}|{m['period']}|{m.get('source', '')}"
