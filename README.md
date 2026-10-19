# 压铸行业全链路情报采集与分析系统

面向压铸（Die Casting）行业的自动化情报系统：定时采集汽车产销、压铸设备、原材料、行业政策等多源信息，经清洗、指标抽取后生成可视化大屏与情报报告。

## 技术栈

Python 3.12 · Playwright · BeautifulSoup / lxml · trafilatura · SQLAlchemy · Flask · Jinja2 · APScheduler · DeepSeek(OpenAI 兼容)

## 快速开始

```bash
# 1. 创建虚拟环境（Python 3.12）
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 2. 安装 Playwright 浏览器内核（微信/动态页采集依赖，必须执行）
.venv/Scripts/python.exe -m playwright install chromium

# 3. 配置密钥（可选，不配也能跑，只是没有 LLM 语义分析）
cp .env.example .env      # 填入 DEEPSEEK_API_KEY

# 4. 一键跑通全链路
.venv/Scripts/python.exe run_pipeline.py
```

启动可视化大屏：

```bash
.venv/Scripts/python.exe web/app.py     # 打开 http://127.0.0.1:5000
```

## 目录结构

```
collectors/   采集层：OICA 新闻/统计、巨潮公告财报、IDC/BCN、微信公众号、新闻正文
processors/   清洗与指标抽取：正文清洗、数值指标正则抽取、按日快照归档
analyzers/    LLM 语义分析（相关度打分、事件抽取）
storage/      SQLite 持久化
web/          Flask 大屏服务与前端
reports/      情报报告生成（HTML / 图表）
config/       数据源配置、关键词词典、单位换算表
data/raw/     原始层（按 YYYY-MM-DD 归档，连续采集证据）
data/clean/   清洗后结构化数据
```

## 采集链路

`run_pipeline.py` 是唯一入口，依次执行：

| 步骤 | 脚本 | 说明 |
|---|---|---|
| OICA 新闻列表 | `collectors/oica_news_collector.py` | 全球汽车产销新闻 |
| OICA 统计数据 | `collectors/oica_stats_collector.py` | 分国别产销数值 |
| 巨潮公告与财报 | `collectors/cninfo_collector.py` | 上市公司公告（PDF 解析） |
| IDC / BCN | `collectors/market_data_collector.py` | 市场份额与机型排名 |
| 公众号采集 | `collectors/wechat_crawler.py` | Playwright + 搜狗微信文章搜索 |
| 新闻正文采集 | `collectors/news_detail_collector.py` | 正文抽取补全 |
| 数值指标抽取 | `processors/metric_extract.py --write` | 正则抽数值进指标库 |
| 原始层按日快照 | `processors/snapshot.py` | 归档到 `data/raw/YYYY-MM-DD/` |
| 生成报告 | `reports/report_builder.py` | 输出 `reports/industry_report_*.html` |

步骤间相互隔离，任一环节失败不会中断整体流程，失败信息写入 `logs/`。

> **注意**：仓库根目录下的 `main.py` 是早期版本，走 `collectors/__init__.py` 的旧连接器注册表（含内置样例数据），**请勿用于正式采集**，一律使用 `run_pipeline.py`。

## 微信公众号采集说明

原计划的「公众号 → RSS」桥接（WeWe RSS）依赖的作者闭源中转服务已永久关停，该路线废弃。现按 Playwright 直采方案实现：

- 通过搜狗微信**「搜文章」**入口按行业关键词检索（「搜公众号」入口已下线）；
- 落地页为 JS 跳转，需等待跳转至 `mp.weixin.qq.com` 后再取值；
- 文章 URL 含易变的 `timestamp`/`signature`，改用页面内 `biz`/`mid`/`idx` 拼规范 URL 作为去重键；
- 卡片日期存在绝对（`2026-9-12`）与相对（`3小时前`）两种格式，已做容错解析；
- 高频请求会触发验证码，脚本自动跳过并记录日志；恢复方式：执行一次 `collectors/wechat_login.py`，人工通过验证后会话自动保存。

## 已知限制

- 连续 7 天采集数据仍在累积中，`data/raw/` 下为已采集日期的原始快照；
- `web/templates/dashboard.html` 尚未实现，大屏 API（`/api/data`）已就绪但缺前端页面；
- 任务书要求的 pandas / pyecharts 尚未引入，当前图表由 matplotlib 生成。

## 数据与隐私

- `.env`（含 API Key）与 `data/wechat_profile/`（浏览器登录态 Cookie）**均已在 `.gitignore` 中排除，不会入库**；
- 如需复现采集，请自行申请 DeepSeek Key 并通过 `wechat_login.py` 建立本地会话。
