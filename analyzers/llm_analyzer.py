import argparse
import json
import os
import re
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from processors.news_processor import CATEGORIES, normalize_value

try:                                  # DeepSeek / OpenAI 兼容(项目默认)
    from openai import OpenAI
except ImportError:
    OpenAI = None
try:                                  # Anthropic Claude(可选,没装不影响 DeepSeek 路径)
    import anthropic
except ImportError:
    anthropic = None

# .env 里放 DEEPSEEK_API_KEY 即可,不用每次手动 export
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
MODEL_CLAUDE = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
# 与 analyzers/event_extractor.py 保持一致(带 /v1)
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
# JSON Mode:强制模型只输出合法 JSON。个别代理网关不支持,会自动降级为普通模式。
# 遇到 400 response_format 报错时设 DEEPSEEK_JSON_MODE=0 即可。
JSON_MODE = os.environ.get("DEEPSEEK_JSON_MODE", "1") not in ("0", "false", "False")
NEWS_PATH = "data/clean/industry_news.json"
CACHE_PATH = "data/clean/analysis.json"

SYSTEM_PROMPT = f"""你是压铸行业情报分析师。判断一条新闻是否与「压铸上下游产业链」相关,并提取结构化要素。

【保留】仅当内容属于以下之一:
1. 镁、铝原材料(价格、供需、产能、政策)
2. 压铸工艺与装备(一体化压铸、Gigacasting、压铸机、模具、材料工艺)
3. 压铸同行企业动态(扩产、订单、财报、并购、技术突破)
4. 直接影响压铸件用量的下游数据:整车销量/产量、新能源车、智能座舱装机量、PC销量、AI眼镜销量

【剔除】泛财经新闻;与压铸件用量无直接关系的汽车周边新闻(如车企合资重组、人事变动、金融政策);无关人事变动。
拿不准时判 false,并在 relevance_reason 说明理由。

只依据提供的标题与正文,不要编造;没有的字段填 null 或空数组。
只输出 JSON,不要任何其他文字,格式:
{{
  "relevant": true或false,
  "relevance_reason": "一句话理由",
  "category": "从以下选一个:{ ' | '.join(CATEGORIES) }",
  "summary_zh": "80字以内中文摘要",
  "bullets": ["要点1", "要点2", "要点3"],
  "event": {{
    "date": "事件发生时间 YYYY-MM-DD,不确定则 null",
    "subject": "事件主体(公司/机构)",
    "amount": 金额或规模的纯数字或null,
    "amount_unit": "原文单位,如 亿元 / 万辆 / million units"
  }},
  "metrics": [
    {{"name": "中文指标名", "period": "YYYY 或 YYYY-MM 或 YYYY-MM-DD 或 YYYY-Qn", "value": 纯数字, "unit": "原文单位"}}
  ]
}}
metrics 只提取文中明确出现的数值(销量、产量、装机量、价格、同比百分比等);同一指标在不同文章中保持相同命名,
例如「中国乘用车零售量」「全球汽车产量」「全球PC出货量」。没有数值则为空数组。"""


def to_number(x):
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


def parse_json(text):
    text = re.sub(r"```json|```", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])


def build_client():
    """按环境挑后端:DeepSeek(OpenAI 兼容)优先,其次 Anthropic Claude。

    返回 (client, backend);都没配返回 (None, None),由调用方明确报错而不是 import 崩溃。
    """
    key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if key and OpenAI is not None:
        return OpenAI(api_key=key, base_url=DEEPSEEK_BASE_URL), "openai"
    if os.environ.get("ANTHROPIC_API_KEY") and anthropic is not None:
        return anthropic.Anthropic(), "anthropic"
    return None, None


def call_llm(client, article, backend="openai"):
    if article.get("status") == "ok" and article.get("content"):
        body, basis = article["content"][:6000], "fulltext"
    else:
        body, basis = "(正文未获取到,请仅依据标题判断;无法确认的字段填 null)", "title_only"

    user_msg = f"标题:{article['title']}\n媒体:{article.get('media', '')}\n\n正文:\n{body}"

    last_err = None
    for attempt in range(3):
        try:
            if backend == "anthropic":
                msg = client.messages.create(
                    model=MODEL_CLAUDE, max_tokens=1500, system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_msg}],
                )
                return parse_json(msg.content[0].text), basis
            kwargs = {
                "model": MODEL,
                "max_tokens": 1500,
                "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                             {"role": "user", "content": user_msg}],
                "temperature": 0,          # 同一篇文章重复跑结果一致,便于验收复现
            }
            # 前两次用 JSON Mode;若网关不支持,第三次自动退化为普通模式
            if attempt < 2 and JSON_MODE:
                kwargs["response_format"] = {"type": "json_object"}
            resp = client.chat.completions.create(**kwargs)
            return parse_json(resp.choices[0].message.content), basis
        except Exception as e:
            last_err = e
            if attempt == 1:
                print(f"  [warn] JSON Mode 不可用,降级为普通模式:{e}")
            time.sleep(2)
    raise last_err


def sanitize(raw):
    category = raw.get("category")
    out = {
        "relevant": bool(raw.get("relevant")),
        "relevance_reason": str(raw.get("relevance_reason") or ""),
        "category": category if category in CATEGORIES else "其他",
        "summary_zh": str(raw.get("summary_zh") or ""),
        "bullets": [str(b) for b in (raw.get("bullets") or [])][:4],
    }

    ev = raw.get("event") or {}
    amount = to_number(ev.get("amount"))
    unit = ev.get("amount_unit")
    event = {
        "date": ev.get("date"),
        "subject": ev.get("subject"),
        "amount": amount,
        "amount_unit": unit,
        "amount_normalized": None,
        "amount_base_unit": None,
    }
    if amount is not None and unit:
        val, base, _ = normalize_value(amount, unit)
        event["amount_normalized"], event["amount_base_unit"] = val, base
    out["event"] = event

    metrics = []
    for m in raw.get("metrics") or []:
        value = to_number(m.get("value"))
        name = (m.get("name") or "").strip()
        if value is None or not name:
            continue
        unit = str(m.get("unit") or "")
        norm, base, recognized = normalize_value(value, unit)
        metrics.append({
            "name": name,
            "period": str(m.get("period") or ""),
            "value": value,
            "unit": unit,
            "value_normalized": norm,
            "base_unit": base,
            "unit_recognized": recognized,
        })
    out["metrics"] = metrics
    return out


def load_cache():
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(cache):
    os.makedirs("data/clean", exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


MAX_CONSEC_FAIL = 5
AUTH_ERROR_HINTS = ("401", "Authentication Fails", "invalid api key",
                    "authentication_error", "Incorrect API key", "insufficient")


def _is_auth_error(e) -> bool:
    """判断是否是 API Key 认证失败/余额不足 —— 这种情况重试整批没有意义。"""
    msg = f"{type(e).__name__}: {e}"
    return any(h.lower() in msg.lower() for h in AUTH_ERROR_HINTS)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50, help="本次最多调用 LLM 的条数")
    args = ap.parse_args()

    client, backend = build_client()
    if client is None:
        print("未配置 LLM:请在 .env 里设置 DEEPSEEK_API_KEY"
              "(或 OPENAI_API_KEY / ANTHROPIC_API_KEY)")
        sys.exit(1)
    print(f"LLM 后端: {backend} / 模型: "
          f"{MODEL if backend == 'openai' else MODEL_CLAUDE}")

    with open(NEWS_PATH, "r", encoding="utf-8") as f:
        articles = json.load(f)

    cache = load_cache()
    calls = 0          # 成功分析并落盘的条数
    attempts = 0       # 含失败在内的总尝试次数(真正用来兜 --limit)
    consec_fail = 0    # 连续失败次数,达阈值直接停,避免空转刷请求

    for a in articles:
        url = a["url"]
        if url in cache:
            continue

        base_info = {
            "url": url, "title": a["title"], "media": a.get("media", ""),
            "status": a.get("status", ""), "image_url": a.get("image_url", ""),
            "first_seen": a.get("first_seen", ""),
            "analyzed_at": datetime.now().isoformat(),
        }

        # 预筛未命中:直接记为不相关,不调用 LLM
        if not a.get("prefilter_pass"):
            cache[url] = {**base_info, "basis": "prefilter", "relevant": False,
                          "relevance_reason": "关键词预筛未命中(未调用LLM)",
                          "category": "其他", "summary_zh": "", "bullets": [],
                          "event": {}, "metrics": []}
            save_cache(cache)
            continue

        # 必须用 attempts 计数。原先只数成功的 calls,一旦持续失败
        # calls 永不增长,会把整批 80+ 条全刷一遍(--limit 3 形同虚设)。
        if attempts >= args.limit:
            print("达到 --limit,剩余留待下次")
            break

        attempts += 1
        try:
            raw, basis = call_llm(client, a, backend)
        except Exception as e:
            print("分析失败:", a["title"][:40], e)
            consec_fail += 1
            if _is_auth_error(e):
                print("\n[中止] API Key 认证失败或余额不足,继续跑只会重复报错。\n"
                      "       请到 https://platform.deepseek.com 检查 key 状态与账户余额,\n"
                      "       确认后更新 .env 里的 DEEPSEEK_API_KEY。")
                break
            if consec_fail >= MAX_CONSEC_FAIL:
                print(f"\n[中止] 连续 {consec_fail} 次失败,疑似网络或服务异常,停止本次运行。")
                break
            continue

        consec_fail = 0
        calls += 1
        cache[url] = {**base_info, "basis": basis, **sanitize(raw)}
        flag = "保留" if cache[url]["relevant"] else "剔除"
        print(f"[{flag}] {a['title'][:50]}")
        save_cache(cache)  # 每条都落盘,中断不丢数据

    save_cache(cache)
    kept = sum(1 for v in cache.values() if v["relevant"])
    print(f"分析完成:本次调用 {calls} 次;累计 {len(cache)} 条,保留 {kept} 条")

    # 一条都没分析成功 = 本次运行无效。必须以非 0 退出,
    # 否则 run_pipeline.py / 计划任务会误以为「LLM分析」这一步成功。
    if calls == 0:
        print("[失败] 本次没有产生任何 LLM 分析结果,请检查 API Key 与网络后再试。")
        sys.exit(1)