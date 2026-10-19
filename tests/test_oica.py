"""最小冒烟测试：验证 OICA 连接器回退逻辑与抽取器可产出指标。"""
from __future__ import annotations

from collectors.oica import OicaConnector
from analyzers.event_extractor import MockExtractor


def test_oica_returns_items():
    conn = OicaConnector(use_sample_on_fail=True)
    items = conn.crawl()
    assert len(items) >= 1
    assert items[0].source == "oica"


def test_mock_extract_produces_metrics():
    conn = OicaConnector(use_sample_on_fail=True)
    item = conn.crawl()[0]
    metrics = MockExtractor().extract(item.content, source="oica")
    assert len(metrics) >= 1
    assert "value" in metrics[0] and "unit" in metrics[0]


if __name__ == "__main__":
    test_oica_returns_items()
    test_mock_extract_produces_metrics()
    print("OK: 连接器与抽取器冒烟测试通过")
