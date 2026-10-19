"""从财报 PDF 文本 / 数据类资讯正文里正则抽取结构化数值指标。

与 LLM 抽取互补:这里只抽「原文明确出现、命名固定、可回溯核对」的高置信指标:
  - 巨潮财报(cninfo):营业收入、归属母公司股东的净利润
  - IDC:全球 PC 出货量、AI 眼镜出货量
  - BCN:日本 PC 销量
数值直接取自原文、不经大模型改写,满足验收「数值与单位可核对」的要求。

自检: python processors/metric_extract.py     # 用现有库跑一遍,只打印不落盘
"""
import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from processors.news_processor import normalize_value

# ========== 巨潮财报 ==========
# 财报表格会被 PDF 排版折行,标签常被数字行从中间切开,例如:
#   归属于上市公司股
#   -215,901,954.17 13,110,046.55 -1,746.84
#   东的净利润
THOUSAND_RE = re.compile(r"\d{1,3}(?:,\d{3})+")            # 带千分位才算数据行
NUM_RE = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+\.\d+")
FRAGMENT_RE = re.compile(r"^[\u4e00-\u9fa5（）()％%／/\-、\s]{1,20}$")
UNIT_LINE_RE = re.compile(r"单位\s*[:：]\s*([千万]?)(元|人民币)")
# 财务数据表确实存在的标志(压平换行后匹配)
FIN_TABLE_HINT_RE = re.compile(r"(会计数据|财务数据|财务指标)")

# (指标名后缀, 标签必须包含, 标签不能包含)
FIN_TARGETS = [
    ("营业收入", ("营业收入", "营业总收入"), ()),
    ("净利润", ("净利润",), ("扣除非经常", "扣非", "母公司", "少数股东", "归属于少数")),
]


def _to_float(s):
    try:
        return float(str(s).replace(",", ""))
    except (TypeError, ValueError):
        return None


def parse_fin_rows(text):
    """把折行的财报表格还原成 [(标签, [数值...], 行号), ...]。

    规则:遇到「带千分位数字」的行即数据行;标签 = 上一行 + 本行数字前的中文
          + (若本行数字在最前面,则再接下一行的纯中文碎片)。
    保留行号是为了后面能取「离该行最近的」单位声明。
    """
    rows, buf = [], ""
    lines = [l.strip() for l in (text or "").splitlines()]
    i = 0
    while i < len(lines):
        line = lines[i]
        m = THOUSAND_RE.search(line)
        if m:
            prefix = line[:m.start()].strip()
            suffix = ""
            # 数字顶格 => 标签被切成了「上一行 + 下一行」两截
            if not prefix and i + 1 < len(lines) and FRAGMENT_RE.match(lines[i + 1]):
                suffix = lines[i + 1].strip()
                i += 1
            nums = [n for n in (_to_float(x) for x in NUM_RE.findall(line)) if n is not None]
            if nums:
                rows.append((buf + prefix + suffix, nums, i))
            buf = ""
        else:
            buf = line
        i += 1
    return rows


def period_from_title(title):
    """文灿股份:2026年半年度报告摘要 -> 2026H1"""
    m = re.search(r"(20\d{2})\s*年", title or "")
    year = m.group(1) if m else ""
    if not year:
        return ""
    # 一/三季度是累计口径(1-3月、1-9月),半年报同理
    if re.search(r"(第一季度|一季报|一季度)", title):
        return f"{year}Q1"
    if re.search(r"(半年度|中期报告|半年报)", title):
        return f"{year}H1"
    if re.search(r"(第三季度|三季报|三季度)", title):
        return f"{year}Q1-Q3"
    return year


def unit_at(text, row_idx):
    """取离该数据行最近的、且在其之前的「单位：X元」声明。

    不能取全文第一个:财报后文的「资产负债表项目(单位:万元)」「债券余额(万元)」
    会把主要会计数据的「元」顶掉,导致营收被放大 1 万倍。
    """
    lines = (text or "").splitlines()
    best = "元"
    for i in range(row_idx, -1, -1):
        m = UNIT_LINE_RE.search(lines[i])
        if m:
            best = m.group(1) + m.group(2)
            break
    return best


def extract_financial_metrics(text, company, title, url):
    """从财报正文抽取营业收入 / 归母净利润。返回可直接入指标库的 dict 列表。

    宁缺勿错:部分财报 PDF 的表格是图片/扫描件,pdfplumber 抽不出文本层,
    此时正文里散落的「净利润为-34,795.11万元」会被误当成表格行,
    量级和单位都是错的。故先确认文本里确实有「主要会计数据/财务」表再抽。
    """
    # PDF 折行会把标记切断(如「主要会计数\n据」),先压平空白再判断
    flat = re.sub(r"\s+", "", text or "")
    if not FIN_TABLE_HINT_RE.search(flat):
        return []
    rows = parse_fin_rows(text)
    if not rows:
        return []

    period = period_from_title(title)
    out = []
    for suffix, must, must_not in FIN_TARGETS:
        for label, nums, idx in rows:
            if not any(k in label for k in must):
                continue
            if any(k in label for k in must_not):
                continue
            value = nums[0]                      # 第 1 列 = 本报告期
            unit = unit_at(text, idx)
            norm, base_unit, ok = normalize_value(value, unit)
            out.append({
                "name": f"{company}{suffix}",
                "period": period,
                "value": value,
                "unit": unit,
                "value_normalized": norm,
                "base_unit": base_unit,
                "unit_recognized": ok,
                "source": "巨潮资讯网",
                "note": label[:60],
                "url": url,
            })
            break                                # 同一指标只取第一个命中行
    return out


# ========== IDC / BCN ==========
EN_PATTERNS = [
    ("全球PC出货量", r"\b(?:worldwide|global)\s+PC\b[\s\S]{0,160}?"
                     r"(\d[\d,]*\.?\d*)\s*(million|billion|thousand)\s+units"),
    ("PC出货量", r"\bPC\b[\s\S]{0,160}?"
                 r"(\d[\d,]*\.?\d*)\s*(million|billion|thousand)\s+units"),
    ("AI眼镜出货量", r"(?:AI|smart|AR/VR|XR)\s*glasses[\s\S]{0,160}?"
                     r"(\d[\d,]*\.?\d*)\s*(million|billion|thousand)\s+units"),
    ("可穿戴设备出货量", r"\bwearable[\s\S]{0,160}?"
                         r"(\d[\d,]*\.?\d*)\s*(million|billion|thousand)\s+units"),
]
# 只认「万台/千台」这类带量级的单位。裸「台」不行——日文稿里
# 「さまざまな用途で活用できる1台だ」这种量词会被当成销量抽出来(实测误抽过 1.0 台)。
# 必须要求「PC 语境」且与数量在同一句([^。] 隔断)。
# 只写「販売台数|出荷台数」会误伤无关品类 —— 实测把 Brother 打印机
# 「シリーズ累計出荷台数は170万台」当成了日本PC销量。
_JP_PC = r"(?:パソコン|ノートPC|ノートパソコン|デスクトップPC|PC市場|国内PC|PC実売|PC出荷)"
JP_PATTERNS = [
    ("日本PC销量", _JP_PC + r"[^。]{0,90}?(?:販売台数|実売台数|出荷台数)?[^。]{0,30}?"
                            r"(\d[\d,]*\.?\d*)\s*(万台|千台)"),
    ("日本PC销量", _JP_PC + r"[^。]{0,40}?台数[^。]{0,30}?(\d[\d,]*\.?\d*)\s*(万台|千台)"),
]
# 「累計出荷台数」是产品生命周期总量,不是当期销量,必须排除
_JP_CUMULATIVE_RE = re.compile(r"累計|計上|歴代|初代から|シリーズ")
# BCN 的「売れてる分析」类文章常只给份额不给绝对台数,单独抽份额(%)
JP_SHARE_PATTERNS = [
    ("日本PC市场份额", r"(?:パソコン|ノートPC|PC)[\s\S]{0,120}?シェア[\s\S]{0,80}?"
                       r"(\d[\d,]*\.?\d*)\s*(％)"),
]
# IDC 的中文/繁体新闻稿(智能眼镜、亚太PC、腕戴设备等)数值形如「出货量148.7万台」。
# 这类 PR 的 URL slug 常是 URL 编码的中文,必须单独走这条路才能抽到。
CN_PATTERNS = [
    ("全球智能眼镜出货量", r"全球智能眼镜[^。\n]{0,24}?出货量\s*([\d,]+(?:\.\d+)?)\s*(万台|亿台)"),
    ("中国智能眼镜出货量", r"中国智能眼镜[^。\n]{0,24}?出货量\s*([\d,]+(?:\.\d+)?)\s*(万台|亿台)"),
    ("全球AR/VR出货量", r"全球AR/VR[^。\n]{0,30}?出货量\s*([\d,]+(?:\.\d+)?)\s*(万台|亿台)"),
    ("中国AR/VR出货量", r"中国AR/VR[^。\n]{0,30}?出货量\s*([\d,]+(?:\.\d+)?)\s*(万台|亿台)"),
    ("全球腕戴设备出货量", r"全球腕戴设备[^。\n]{0,30}?出货\s*([\d,]+(?:\.\d+)?)\s*(亿台|万台)"),
    ("全球PC出货量", r"全球(?:传统)?\s*PC[^。\n]{0,30}?出货量[^。\n]{0,10}?([\d,]+(?:\.\d+)?)\s*(万台|亿台)"),
    ("亚太PC出货量", r"亚太[^。\n]{0,20}?PC[^。\n]{0,20}?出货量[^。\n]{0,12}?([\d,]+(?:\.\d+)?)\s*(万台|亿台)"),
]
Q_PERIOD_RE = re.compile(r"\b(Q[1-4])\s*(20\d{2})\b|\b(20\d{2})\s*(Q[1-4])\b", re.I)
JP_PERIOD_RE = re.compile(r"(20\d{2})[年/\.](\d{1,2})[月]?")
# 中文稿的期间写在正文里:「2025年第三季度」「2025年前三季度」。
# 不能 fallback 到发布日期 —— 发布会晚于数据期一个月以上,期间会错。
CN_PERIOD_RE = re.compile(r"(20\d{2})\s*年\s*(前)?\s*(?:第)?\s*([一二三四1-4])\s*季度")
_CN_QMAP = {"一": "Q1", "1": "Q1", "二": "Q2", "2": "Q2",
            "三": "Q3", "3": "Q3", "四": "Q4", "4": "Q4"}


def cn_period_from_text(text):
    """从中文正文解析期间。返回 '' 表示没找到。"""
    m = CN_PERIOD_RE.search(text or "")
    if not m:
        return ""
    q = _CN_QMAP.get(m.group(3), "")
    if not q:
        return ""
    if m.group(2):                      # 「前三季度」是累计口径
        return f"{m.group(1)}Q1-{q}"
    return f"{m.group(1)}{q}"


def _lang_of(text, title=""):
    """判定语种:中日文都含汉字,靠假名区分。"""
    head = f"{title} {text[:800]}"
    if re.search(r"[\u3040-\u30ff]", head):      # 平假名/片假名
        return "ja"
    if re.search(r"[\u4e00-\u9fa5]", head):
        return "zh"
    return "en"


def extract_market_metrics(text, source, url, publish_date="", title=""):
    """从 IDC / BCN 资讯正文抽取出货量类数值。

    语种路由:IDC 会同时发英文 / 中文简体 / 中文繁体 / 日文稿,三种格式的数值写法不同。
    """
    text = text or ""
    lang = _lang_of(text, title)

    if lang == "ja":
        # BCN 额外带上份额(%):这类文章常常只给份额、不给绝对台数
        patterns = JP_PATTERNS + JP_SHARE_PATTERNS
    elif lang == "zh":
        patterns = CN_PATTERNS + EN_PATTERNS
    else:
        patterns = EN_PATTERNS

    # 期间优先级:标题 -> 中文正文季度 -> 英文 Qn YYYY -> 日文年月 -> 发布日期
    # 发布日期是最后兜底:新闻发布普遍滞后于数据期一个月以上,直接当期间会错。
    period = period_from_title(title) if period_from_title(title) else ""
    if not period:
        period = cn_period_from_text(text)
    if not period:
        m = Q_PERIOD_RE.search(text)
        if m:
            period = (m.group(2) or m.group(3)) + (m.group(1) or m.group(4)).upper()
    if not period:
        jm = JP_PERIOD_RE.search(text) or JP_PERIOD_RE.search(publish_date or "")
        period = f"{jm.group(1)}-{int(jm.group(2)):02d}" if jm else (publish_date or "")[:7]

    out, seen = [], set()
    for name, pat in patterns:
        if name in seen:
            continue
        for m in re.finditer(pat, text, re.I):
            snippet = m.group(0)
            # 「累計出荷台数」是生命周期总量而非当期销量,直接丢弃
            if lang == "ja" and _JP_CUMULATIVE_RE.search(snippet):
                continue
            value = _to_float(m.group(1))
            if value is None:
                continue
            g2 = m.group(2) if (m.lastindex or 0) >= 2 else ""
            if g2 == "％":
                unit = "%"
            elif g2.lower() in ("million", "billion", "thousand"):
                unit = f"{g2.lower()} units"
            else:
                unit = g2                        # 万台 / 千台 / 亿台 原样保留
            norm, base_unit, ok = normalize_value(value, unit)
            out.append({
                "name": name,
                "period": period,
                "value": value,
                "unit": unit,
                "value_normalized": norm,
                "base_unit": base_unit,
                "unit_recognized": ok,
                "source": source,
                "note": m.group(0)[-70:],
                "url": url,
            })
            seen.add(name)
            break                                # 同一指标取首个命中

    # 同一数值会被不同粒度的名称重复命中(如「全球PC出货量」和「PC出货量」),
    # 只保留最具体的那个(EN_PATTERNS 里越靠前越具体)。
    dedup = {}
    for m in out:
        dedup.setdefault((m["period"], m["value"]), m)
    return list(dedup.values())


# ========== 自检 / 回填 ==========
def iter_metrics_from_store(path="data/clean/industry_news.json"):
    """对已有条目跑一遍抽取,产出指标(不写库)。"""
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        items = json.load(f)
    out = []
    for x in items:
        if x.get("source_type") == "financial":
            ms = extract_financial_metrics(x.get("content", ""),
                                           x.get("company") or x.get("title", "").split(":")[0],
                                           x.get("title", ""), x.get("url", ""))
        elif x.get("source") in ("IDC", "BCN"):
            ms = extract_market_metrics(x.get("content", ""), x.get("source"),
                                        x.get("url", ""), x.get("publish_date", ""),
                                        x.get("title", ""))
        else:
            continue
        for m in ms:
            m.setdefault("collected_at", datetime.now().isoformat())
        out.extend(ms)
    return out


if __name__ == "__main__":
    from processors.store import load_metrics, save_metrics, metric_key

    write = "--write" in sys.argv
    ms = iter_metrics_from_store()
    for m in ms:
        print(f"{m['period']:8} {m['name']:16} {m['value']:>18,.2f} "
              f"{m['unit']:14} -> {m['value_normalized']:>18,.0f} "
              f"{m['base_unit']} [{m['source']}]")
    print(f"共抽取 {len(ms)} 条")

    if write and ms:
        store = load_metrics()
        for m in ms:
            store[metric_key(m)] = m
        save_metrics(store)
        print(f"已写入 data/clean/metrics.json,指标库现共 {len(store)} 条")
    elif not write:
        print("(只打印;加 --write 才写入指标库)")
