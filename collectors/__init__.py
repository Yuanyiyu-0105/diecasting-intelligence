"""采集器注册表：按 sources.yaml 动态装配启用的源。"""
from __future__ import annotations

from .oica import OicaConnector
from .idc import IdcConnector

try:  # 微信公众号占位实现尚未落地，缺文件不应让 main.py 整体崩掉
    from .wechat_collector import WechatConnector
except ImportError:
    WechatConnector = None  # 微信走独立脚本 collectors/wechat_rss_collector.py

REGISTRY = {
    "oica": OicaConnector,
    "idc": IdcConnector,
}
if WechatConnector is not None:
    REGISTRY["wechat"] = WechatConnector


def build_connectors(sources_cfg: dict, settings: dict) -> list:
    """根据配置返回启用源的 Connector 实例列表。"""
    out = []
    timeout = settings.get("crawl_timeout", 15)
    use_sample = settings.get("use_sample_on_fail", True)
    for key, cfg in sources_cfg.items():
        if not cfg.get("enabled", True):
            continue
        cls = REGISTRY.get(key)
        if cls is None:
            print(f"[collectors] 未找到采集器实现：{key}，跳过")
            continue
        out.append(cls(timeout=timeout, use_sample_on_fail=use_sample))
    return out
