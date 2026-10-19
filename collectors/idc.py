"""IDC 采集器（阶段 1：真实抓取中文媒体报道，覆盖 PC 与智能眼镜出货量）。

真实路径：IDC 官网首页无中文指标，改为抓取转载其数据的两篇中文报道
         - PC 季度出货量（含「XXXX 万台 / 同比 X%」）
         - 智能眼镜年度出货量（含「XXXX 万台 / 同比 X%」）
        这样相关性过滤能命中、抽取器能产出真实指标，告别「过滤 1 条」。
MVP 阶段若抓取失败，回退到内置样例数据。
"""
from __future__ import annotations

from bs4 import BeautifulSoup

from .web_collector import BaseConnector, RawItem

# 真实中文报道（转载 IDC 数据），含可抽取的出货量指标
IDC_URLS = [
    "https://www.cnfin.com/gs-lb/detail/20251010/4314785_1.html",  # 2025 Q3 全球 PC 出货量
    "https://wap.ce.cn/macro/more/202603/t20260326_2858299.shtml",  # 2025 全球/中国智能眼镜出货量
]

IDC_SAMPLE = [
    RawItem(
        source="idc",
        url="https://www.cnfin.com/gs-lb/detail/20251010/4314785_1.html",
        title="2025 Q3 全球传统 PC 出货量",
        publish_time="2025-10-09",
        content=(
            "IDC 最新数据显示，2025 年第三季度全球传统 PC 出货量同比下降 1.0% 至 6840 万台。"
            "尽管需求疲软，商用换机周期支撑了整体出货量。"
        ),
        is_sample=True,
    ),
    RawItem(
        source="idc",
        url="https://wap.ce.cn/macro/more/202603/t20260326_2858299.shtml",
        title="2025 全球 AI 眼镜出货量预测",
        publish_time="2025-11-20",
        content=(
            "IDC 预计 2025 年全球 AI 眼镜出货量将达到 1200 万台，同比增长 45%。"
            "随着端侧大模型落地，AI 眼镜成为消费电子增长最快的细分品类之一。"
        ),
        is_sample=True,
    ),
]


class IdcConnector(BaseConnector):
    name = "idc"
    url = "https://www.idc.com/"

    def crawl(self) -> list[RawItem]:
        items = []
        try:
            for url in IDC_URLS:
                try:
                    html = self._fetch(url)
                except Exception as e:  # noqa: BLE001
                    print(f"[idc] 单页抓取失败（{url}）：{e}")
                    continue
                soup = BeautifulSoup(html, "lxml")
                paras = [p.get_text(strip=True) for p in soup.select("p") if p.get_text(strip=True)]
                text = "\n".join(paras[:50])
                if len(text) < 50:
                    continue
                items.append(
                    RawItem(source="idc", url=url, title="IDC 快讯", publish_time="", content=text)
                )
            if items:
                return items
            raise ValueError("IDC 所有页面均未解析到正文")
        except Exception as e:  # noqa: BLE001
            if self.use_sample_on_fail:
                print(f"[idc] 实时抓取失败（{e}），回退样例数据")
                return list(IDC_SAMPLE)
            raise
