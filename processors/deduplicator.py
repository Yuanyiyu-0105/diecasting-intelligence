"""去重模块（阶段 2）：基于 URL 哈希判断文章是否已收录。

提供纯函数 content_hash(url)，供存储层在写入 articles 表前计算 url_hash，
配合 SQLite 的 UNIQUE(url_hash) 约束实现幂等入库（重跑不产生重复文章）。
"""
from __future__ import annotations

import hashlib


def content_hash(url: str) -> str:
    """对文章 URL 做 MD5 哈希，作为去重主键。"""
    return hashlib.md5(url.encode("utf-8")).hexdigest()
