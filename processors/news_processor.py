import re
import unicodedata
from urllib.parse import urlparse

# 报告和 LLM 共用的类别
CATEGORIES = ["原材料(镁铝)", "压铸工艺与装备", "同行企业动态",
              "下游-汽车销量", "下游-3C销量", "其他"]

# ========== 媒体识别 ==========
MEDIA_MAP = {
    "nikkei.com": "Nikkei Asia",
    "ft.com": "Financial Times",
    "automobilwoche.de": "Automobilwoche",
    "autonews.com": "Automotive News",
    "gasgoo.com": "Gasgoo",
    "oica.net": "OICA",
    "reuters.com": "Reuters",
    "bloomberg.com": "Bloomberg",
    "cnevpost.com": "CnEVPost",
    "electrive.com": "electrive",
}
PAYWALL_MEDIA = {"Nikkei Asia", "Financial Times", "Bloomberg"}


def clean_url(url):
    m = re.search(r"\((https?://[^)\s]+)\)", url)  # 处理混入 Markdown 的 URL
    if m:
        url = m.group(1)
    return url.strip()


def identify_media(url):
    host = urlparse(url).netloc.lower().split(":")[0]
    for domain, name in MEDIA_MAP.items():
        if host == domain or host.endswith("." + domain):
            return name
    return host or "unknown"


# ========== 正文清洗 ==========
NOISE_RE = re.compile(
    r"(cookie|subscribe|subscription|sign in|log in|newsletter|advertisement"
    r"|all rights reserved|privacy policy|terms of (use|service)|read more"
    r"|share this|follow us|enable javascript|abonnieren|anmelden"
    r"|datenschutz|impressum|werbung|订阅|登录|版权所有|广告)",
    re.I,
)


def clean_text(text, max_len=8000):
    text = unicodedata.normalize("NFKC", text).replace("\xa0", " ")
    seen, lines = set(), []
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if len(line) < 20:
            continue
        if len(line) < 120 and NOISE_RE.search(line):
            continue
        if line in seen:
            continue
        seen.add(line)
        lines.append(line)
    return "\n".join(lines)[:max_len]


# ========== 关键词预筛(偏宽松,最终相关性由 LLM 判断) ==========
CORE_KEYWORDS = {
    "压铸": ["die casting", "die-casting", "diecasting", "high pressure die casting",
             "hpdc", "druckguss", "压铸"],
    "一体化压铸": ["integrated die casting", "integrated die-casting", "one-piece casting",
                   "single-piece casting", "unibody casting", "一体化压铸", "一体压铸"],
    "Gigacasting": ["gigacasting", "giga casting", "gigapress", "giga press",
                    "megacasting", "mega casting"],
    "铝合金": ["aluminum alloy", "aluminium alloy", "aluminum", "aluminium",
               "aluminiumlegierung", "铝合金", "铝锭", "铝材"],
    "镁合金": ["magnesium", "magnesiumlegierung", "镁合金", "镁锭", "镁"],
    "电池壳体": ["battery housing", "battery enclosure", "battery tray", "battery case",
                 "battery casing", "batteriegehäuse", "batteriekasten",
                 "电池壳体", "电池包壳体", "电池托盘"],
}
DOWNSTREAM_KEYWORDS = {
    "电动车": ["electric vehicle", "electric vehicles", "electric car", "ev", "evs", "bev",
               "nev", "new energy vehicle", "elektroauto", "elektrofahrzeug",
               "电动车", "电动汽车", "新能源汽车"],
    "汽车销量": ["car sales", "vehicle sales", "auto sales", "passenger car", "retail sales",
                 "vehicle production", "汽车销量", "乘用车", "零售", "批发", "汽车产量"],
    "智能座舱": ["smart cockpit", "digital cockpit", "cockpit", "智能座舱", "座舱"],
    "PC销量": ["pc shipments", "pc market", "pc sales", "personal computer",
               "笔记本", "个人电脑", "pc出货", "pc 出货"],
    "AI眼镜": ["ai glasses", "smart glasses", "ai眼镜", "智能眼镜"],
}
GROUP_WEIGHT = {"core": 3, "downstream": 1}
PREFILTER_MIN_SCORE = 1


def _compile(alias):
    return re.compile(r"(?<![A-Za-z])" + re.escape(alias) + r"(?![A-Za-z])", re.I)


_PATTERNS = {
    group: {kw: [_compile(a) for a in aliases] for kw, aliases in kws.items()}
    for group, kws in {"core": CORE_KEYWORDS, "downstream": DOWNSTREAM_KEYWORDS}.items()
}


def extract_keywords(text):
    """返回 ({'core': {词: 次数}, 'downstream': {...}}, 预筛分数)"""
    hits = {"core": {}, "downstream": {}}
    score = 0
    for group, kws in _PATTERNS.items():
        for kw, patterns in kws.items():
            count = sum(len(p.findall(text)) for p in patterns)
            if count:
                hits[group][kw] = count
                score += GROUP_WEIGHT[group] * min(count, 5)
    return hits, score


# ========== 单位标准化(对齐不同平台的数值单位) ==========
_CN_SCALE = {"": 1, "千": 1e3, "万": 1e4, "百万": 1e6, "千万": 1e7,
             "亿": 1e8, "十亿": 1e9, "万亿": 1e12}
_EN_SCALE = {"": 1, "thousand": 1e3, "k": 1e3, "million": 1e6, "m": 1e6, "mn": 1e6,
             "billion": 1e9, "bn": 1e9, "b": 1e9, "trillion": 1e12}
_UNIT_DEFS = [
    ("units", ["辆", "台"], ["unit", "units", "vehicle", "vehicles", "car", "cars"]),
    ("CNY", ["元", "人民币"], ["yuan", "rmb", "cny"]),
    ("USD", ["美元"], ["usd", "dollar", "dollars"]),
    ("EUR", ["欧元"], ["eur", "euro", "euros"]),
    ("JPY", ["日元"], ["jpy", "yen"]),
    ("t", ["吨"], ["tonne", "tonnes", "ton", "tons"]),
]
UNIT_TABLE = {}
for _base, _cn, _en in _UNIT_DEFS:
    for _s, _f in _CN_SCALE.items():
        for _n in _cn:
            UNIT_TABLE[_s + _n] = (_f, _base)
    for _s, _f in _EN_SCALE.items():
        for _n in _en:
            UNIT_TABLE[(_s + " " + _n).strip()] = (_f, _base)
for _p in ("%", "percent", "pct", "per cent"):
    UNIT_TABLE[_p] = (1, "%")


def normalize_value(value, unit):
    """返回 (标准化数值, 基础单位, 是否识别)。
    例: (350, '万辆') -> (3500000, 'units', True)
    货币只统一量级,不做汇率换算。"""
    key = re.sub(r"\s+", " ",
                 unicodedata.normalize("NFKC", str(unit or "")).strip().lower())
    hit = UNIT_TABLE.get(key)
    if hit:
        factor, base = hit
        return value * factor, base, True
    return value, unit or "", False