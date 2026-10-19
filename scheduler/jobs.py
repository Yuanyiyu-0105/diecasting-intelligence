"""调度器（阶段 7）：APScheduler 每日定时触发全链路 Pipeline。

生产环境换 Airflow DAG。任务幂等：重跑不产生重复文章（靠 URL 哈希去重）。
"""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from apscheduler.schedulers.blocking import BlockingScheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("scheduler")


def run_daily_job():
    """每天跑一次主链路 run_pipeline.py。

    不用 main.run_pipeline:那条链路走的是旧 connectors(oica.py/idc.py 样例/二手数据)。
    这里用 subprocess 调用 run_pipeline.py,失败也能完整捕获日志。
    """
    script = ROOT / "run_pipeline.py"
    logger.info("定时任务触发:开始全链路采集")
    r = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(ROOT),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"},
    )
    logger.info(f"run_pipeline.py exit={r.returncode}")
    if r.stdout:
        logger.info(r.stdout)
    if r.stderr:
        logger.error(r.stderr)
    logger.info("定时任务完成")


def start(cron_hour: int = 8, cron_minute: int = 0):
    sched = BlockingScheduler()
    sched.add_job(run_daily_job, "cron", hour=cron_hour, minute=cron_minute)
    logger.info(f"调度器已启动,每日 {cron_hour:02d}:{cron_minute:02d} 触发。Ctrl+C 退出。")
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("调度器已停止")


if __name__ == "__main__":
    start()
