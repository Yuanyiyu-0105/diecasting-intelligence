import os
import subprocess
import sys
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)

# 前面的采集步骤失败不影响后面的步骤,失败信息写入日志
STEPS = [
    ("OICA新闻列表", ["collectors/oica_news_collector.py"]),
    ("OICA统计数据", ["collectors/oica_stats_collector.py"]),
    ("巨潮公告与财报", ["collectors/cninfo_collector.py"]),
    ("IDC/BCN", ["collectors/market_data_collector.py"]),
    # 公众号采集：RSS 桥接上游(WeWe RSS 中转)已永久关停，改为任务书规定的
    # Playwright 搜狗微信路线。撞到验证码会自动跳过并记日志（不计入失败之外的处理），
    # 需人工跑一次 collectors/wechat_login.py 恢复会话。
    ("公众号采集", ["collectors/wechat_crawler.py"]),
    ("新闻正文采集", ["collectors/news_detail_collector.py"]),
    # 正则抽取:把新闻正文里的数值抽进 data/clean/metrics.json。
    # 必须排在「生成报告」之前,否则报告「五、权威统计」章节取不到当天新指标。
    ("数值指标抽取", ["processors/metric_extract.py", "--write"]),
    # 原始层按日归档:各采集器此前要么不写 raw,要么写死固定文件名被每日覆盖,
    # 攒不出「连续 7 天」证据。统一在每天跑完后按 data/raw/YYYY-MM-DD/ 归档。
    ("原始层按日快照", ["processors/snapshot.py"]),
    ("生成报告", ["reports/report_builder.py"]),
]

# 可选步骤:需要有效的 LLM API Key,没有就跳过(不计入失败)
LLM_STEP = ("LLM分析", ["analyzers/llm_analyzer.py"])
LLM_KEY_VARS = ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")


def _looks_placeholder(v):
    return (not v) or v.startswith("sk-xxxx") or "your-key" in v


def has_llm_key():
    """检测是否配置了可用的 LLM Key。

    先看环境变量,再退回读 .env —— 与 analyzers/llm_analyzer.py 的取值顺序保持一致。
    """
    for name in LLM_KEY_VARS:
        if not _looks_placeholder((os.environ.get(name) or "").strip()):
            return True
    env_path = os.path.join(ROOT, ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                for name in LLM_KEY_VARS:
                    if line.startswith(name + "="):
                        v = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if not _looks_placeholder(v):
                            return True
    return False


def llm_enabled():
    """LLM 步骤的总开关:默认关闭,需显式 ENABLE_LLM=1 且配好 key。"""
    flag = (os.environ.get("ENABLE_LLM") or "0").strip().lower()
    return flag in ("1", "true", "yes") and has_llm_key()


os.makedirs("logs", exist_ok=True)
log_path = f"logs/pipeline_{datetime.now().strftime('%Y%m%d')}.log"
env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
failed = []

# LLM 步骤默认关闭 —— 当前阶段的验收只要求「采集 + 抽取」跑通,
# 且现有 key 返回 401,留着反而让每天的计划任务日志挂一条无意义失败。
# 需要启用时:在 .env 里配好有效 key,并设置 ENABLE_LLM=1。
steps = list(STEPS)
llm_on = llm_enabled()
if llm_on:
    idx = next(i for i, (n, _) in enumerate(steps) if n == "生成报告")
    steps.insert(idx, LLM_STEP)
elif has_llm_key():
    print(">>> LLM分析:已跳过(需在 .env 中同时设置 ENABLE_LLM=1)")
else:
    print(">>> LLM分析:未检测到 API Key,本次跳过")

with open(log_path, "a", encoding="utf-8") as log:
    log.write(f"\n===== {datetime.now().isoformat()} =====\n")
    if not llm_on:
        log.write("--- LLM分析 (skipped: no api key) ---\n")
    for name, script in steps:
        print(f">>> {name}")
        r = subprocess.run([sys.executable] + script, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", env=env)
        log.write(f"--- {name} (exit {r.returncode}) ---\n{r.stdout}\n{r.stderr}\n")
        print(r.stdout)
        if r.returncode != 0:
            failed.append(name)
            print(r.stderr)
    log.write(f"失败步骤: {failed or '无'}\n")

print("失败步骤:", failed or "无")
sys.exit(1 if failed else 0)
