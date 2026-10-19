"""语义抽取（阶段 4）：从正文抽取结构化指标 + 单位对齐。

主路径：DeepSeek（OpenAI 兼容接口，JSON Mode 稳定输出）。
无 API Key 时自动回退 MockExtractor（正则启发式），保证全链路可跑通。

抽取字段 schema：
    {event_date, subject, event, value, unit, yoy, source, related_to_diecasting}
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# 数值+单位正则：覆盖 万辆/万台/千台/亿元/万元/辆/台/%
# 注意：% 不作为独立指标单位（它是同比比率，由 _YOY 单独抽取），否则会误抽成多余指标
_NUM_UNIT = re.compile(r"(\d+(?:\.\d+)?)\s*(万辆|万台|千台|亿辆|亿元|万元|辆|台)")
# 同比与数字之间可能有「增长/下降/上升」等字，用 [^0-9]*? 跨过
_YOY = re.compile(r"同比[^0-9]*?([+-]?\d+(?:\.\d+)?)\s*%")
_YEAR = re.compile(r"(20\d{2})")


def _classify(window: str) -> tuple[str, str]:
    """根据匹配窗口判断 event（产量/销量/出货量）与 subject（全球/中国/...）。"""
    if "产量" in window:
        event = "产量"
    elif "销量" in window:
        event = "销量"
    elif "出货量" in window:
        event = "出货量"
    else:
        event = "指标"

    if "中国" in window:
        subject = "中国"
    elif "新能源" in window:
        subject = "新能源汽车"
    elif "AI眼镜" in window or "AI 眼镜" in window:
        subject = "AI眼镜"
    elif "传统" in window and ("PC" in window or "pc" in window):
        subject = "传统PC"
    elif "PC" in window or "pc" in window:
        subject = "PC"
    elif "全球" in window:
        subject = "全球"
    else:
        subject = "行业"
    return subject, event


class MockExtractor:
    """无 Key 时的启发式抽取，专门让样例数据产出可读指标。"""

    def extract(self, content: str, source: str = "") -> list[dict]:
        metrics: list[dict] = []
        year = _YEAR.search(content)
        base_year = year.group(1) if year else "2025"

        for m in _NUM_UNIT.finditer(content):
            value = float(m.group(1))
            unit = m.group(2)
            # 主体/事件判定只看数字「之前」的上下文，避免把下一句的词误带进来
            window = content[max(0, m.start() - 30) : m.start()]
            subject, event = _classify(window)

            # 同比窗口收紧到数字紧邻 ±15，正负号看匹配终点之前是否含「下降/减少」
            yoy_win = content[max(0, m.start() - 15) : m.end() + 15]
            yoy_m = _YOY.search(yoy_win)
            yoy = ""
            if yoy_m:
                sign = "-" if re.search(r"下降|减少|下滑|负", yoy_win[: yoy_m.end()]) else "+"
                yoy = f"{sign}{yoy_m.group(1)}%"

            metrics.append(
                {
                    "event_date": base_year,
                    "subject": subject,
                    "event": event,
                    "value": value,
                    "unit": unit,
                    "yoy": yoy,
                    "source": source,
                    "related_to_diecasting": True,
                }
            )
        return metrics


class DeepSeekExtractor:
    """DeepSeek JSON Mode 抽取。"""

    def __init__(self):
        from openai import OpenAI

        self.client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
        self.model = DEEPSEEK_MODEL

    def extract(self, content: str, source: str = "") -> list[dict]:
        prompt = (
            "你是压铸行业情报分析助手。从给定文本中抽取所有与汽车产销、PC/消费电子出货量、"
            "AI眼镜相关的结构化指标。每个指标输出 JSON 对象，字段：\n"
            "event_date(年份,如2025), subject(主体,如全球/中国/新能源汽车/AI眼镜), "
            "event(事件,如产量/销量/出货量), value(数值,数字), unit(单位,如万辆/万台/%), "
            "yoy(同比,如+3.5%或空字符串), source(来源名), related_to_diecasting(布尔)。\n"
            "只输出 JSON，格式：{\"metrics\": [ ... ]}。若没有相关指标返回 {\"metrics\": []}。\n\n"
            f"文本：\n{content}"
        )
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.1,
            )
            data = json.loads(resp.choices[0].message.content)
            metrics = data.get("metrics", [])
            for m in metrics:
                m["source"] = m.get("source") or source
            return metrics
        except Exception as e:  # noqa: BLE001
            print(f"[extract] DeepSeek 调用失败（{e}），回退 Mock 抽取")
            return MockExtractor().extract(content, source)


def build_extractor() -> object:
    """有 Key 用 DeepSeek，否则 Mock。"""
    if DEEPSEEK_API_KEY and DEEPSEEK_API_KEY.startswith("sk-") and "your-key" not in DEEPSEEK_API_KEY:
        try:
            return DeepSeekExtractor()
        except Exception as e:  # noqa: BLE001
            print(f"[extract] DeepSeek 初始化失败（{e}），使用 Mock")
    print("[extract] 未配置有效 DEEPSEEK_API_KEY，使用 Mock 抽取器（先跑通全链路）")
    return MockExtractor()
