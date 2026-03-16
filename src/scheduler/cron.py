"""
APScheduler 定时任务调度器
每日定时触发 run_workflow
"""
import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.config import settings
from src.scheduler.workflow import run_workflow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    scheduler = AsyncIOScheduler()
    schedule_hours = ",".join(str(h) for h in settings.schedule_hours)
    scheduler.add_job(
        run_workflow,
        trigger=CronTrigger(
            hour=schedule_hours,
            minute=settings.schedule_minute,
            timezone="Asia/Shanghai",
        ),
        id="daily_tweet",
        name="每日推文工作流",
        max_instances=1,
        misfire_grace_time=300,
    )
    scheduler.start()
    hours_str = schedule_hours
    logger.info(
        "调度器已启动，每天 %s:%02d 北京时间执行",
        hours_str,
        settings.schedule_minute,
    )

    try:
        await asyncio.Event().wait()  # 永久等待，直到 Ctrl+C
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        logger.info("调度器已停止")


if __name__ == "__main__":
    asyncio.run(main())
