"""
每日 Markdown 文件存储模块
"""
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config import settings

logger = logging.getLogger(__name__)


def get_daily_md_path(run_at: datetime) -> Path:
    """获取当天 Markdown 文件路径"""
    date_str = run_at.strftime("%Y-%m-%d")
    settings.daily_path.mkdir(parents=True, exist_ok=True)
    return settings.daily_path / f"{date_str}.md"


def sync_to_target(md_file: Path) -> None:
    """同步 Markdown 文件到目标目录"""
    sync_target = settings.sync_target
    if sync_target:
        sync_target.mkdir(parents=True, exist_ok=True)
        target_path = sync_target / md_file.name
        shutil.copy2(md_file, target_path)
        logger.info("已同步到: %s", target_path)


def write_daily_md(
    run_at: datetime,
    tweets: list[dict[str, Any]],
    news_items: list[Any],
    summary: str | None = None,
) -> Path:
    """
    将每日抓取的新闻和发布的推文写入 Markdown 文件

    Args:
        run_at: 运行时间
        tweets: 已发布的推文列表
        news_items: 抓取的新闻列表
        summary: LLM 生成的每日总结

    Returns:
        写入的文件路径
    """
    settings.daily_path.mkdir(parents=True, exist_ok=True)

    date_str = run_at.strftime("%Y-%m-%d")
    md_file = settings.daily_path / f"{date_str}.md"

    # 按来源统计
    reddit_count = sum(1 for item in news_items if item.source == "reddit")
    deduped_count = len(news_items)

    # 按分类整理新闻
    politics_news = [item for item in news_items if item.category.value == "politics"]
    tech_news = [item for item in news_items if item.category.value == "tech"]
    other_news = [item for item in news_items if item.category.value == "unknown"]

    # 构建 Markdown 内容
    lines = [
        f"# 每日推文 - {date_str}",
        "",
        "## 抓取概览",
        f"- Reddit: {reddit_count} 条",
        f"- 去重后: {deduped_count} 条",
        "",
    ]

    # 抓取的新闻
    lines.append("## 抓取的新闻")
    lines.append("")

    if politics_news:
        lines.append("### 时政 (Politics)")
        for i, item in enumerate(politics_news, 1):
            lines.append(f"{i}. [{item.title}]({item.url}) - 评分: {item.score}")
        lines.append("")

    if tech_news:
        lines.append("### 科技 (Tech)")
        for i, item in enumerate(tech_news, 1):
            lines.append(f"{i}. [{item.title}]({item.url}) - 评分: {item.score}")
        lines.append("")

    if other_news:
        lines.append("### 其他")
        for i, item in enumerate(other_news, 1):
            lines.append(f"{i}. [{item.title}]({item.url}) - 评分: {item.score}")
        lines.append("")

    # 每日总结（避免重复添加）
    if summary:
        # 检查是否已存在每日总结
        has_summary = any("## 每日总结" in line for line in lines)
        if not has_summary:
            lines.append("## 每日总结")
            lines.append("")
            lines.append(summary)
            lines.append("")

    # 发布的推文
    lines.append("## 发布的推文")
    lines.append("")

    for i, entry in enumerate(tweets, 1):
        lines.append(f"### 推文 {i}")
        lines.append("")
        lines.append(f"> {entry['tweet']}")
        lines.append("")
        lines.append(f"- 来源: {entry['source']}/{entry.get('source_sub', 'unknown')}")
        lines.append(f"- 原文标题: {entry['headline']}")
        lines.append(f"- 分类: {entry['category']}")
        lines.append(f"- 字数: {entry['char_count']}")
        lines.append(f"- 发布状态: {'✅' if entry['published'] else '❌'}")
        if entry.get("tweet_id"):
            lines.append(f"- Tweet ID: {entry['tweet_id']}")
        lines.append("")

    # 写入文件
    md_file.write_text("\n".join(lines), encoding="utf-8")
    logger.info("每日 Markdown 已写入: %s", md_file)

    # 同步到目标目录
    sync_to_target(md_file)

    return md_file


def update_daily_md_incremental(
    run_at: datetime,
    tweet_entry: dict[str, Any],
    news_items: list[Any],
) -> Path:
    """
    增量更新当天的 Markdown 文件（追加单条推文）

    Args:
        run_at: 运行时间
        tweet_entry: 单条推文日志条目
        news_items: 抓取的新闻列表（用于获取抓取概览）

    Returns:
        写入的文件路径
    """
    md_file = get_daily_md_path(run_at)
    date_str = run_at.strftime("%Y-%m-%d")

    if not md_file.exists():
        # 文件不存在，创建新文件（包含抓取概览）
        news_count = len(news_items)
        reddit_count = sum(1 for item in news_items if item.source == "reddit")

        lines = [
            f"# 每日推文 - {date_str}",
            "",
            "## 抓取概览",
            f"- Reddit: {reddit_count} 条",
            f"- 去重后: {news_count} 条",
            "",
            "## 发布的推文",
            "",
        ]
        md_file.write_text("\n".join(lines), encoding="utf-8")

    # 读取现有内容，追加新推文
    existing_content = md_file.read_text(encoding="utf-8")
    lines = existing_content.split("\n")

    # 找到 "## 发布的推文" 部分的末尾
    insert_idx = len(lines)
    for i, line in enumerate(lines):
        if line.strip() == "## 发布的推文":
            # 找到推文部分，从下一行开始找插入点
            for j in range(i + 1, len(lines)):
                if lines[j].startswith("## "):
                    insert_idx = j
                    break
            else:
                insert_idx = len(lines)
            break

    # 构建新推文内容
    # 计算当前推文编号
    tweet_num = 1
    for line in lines:
        if line.startswith("### 推文 "):
            try:
                num = int(line.split("推文 ")[1].strip())
                if num >= tweet_num:
                    tweet_num = num + 1
            except (ValueError, IndexError):
                pass

    new_tweet_lines = [
        f"### 推文 {tweet_num}",
        "",
        f"> {tweet_entry['tweet']}",
        "",
        f"- 来源: {tweet_entry['source']}/{tweet_entry.get('source_sub', 'unknown')}",
        f"- 原文标题: {tweet_entry['headline']}",
        f"- 分类: {tweet_entry['category']}",
        f"- 字数: {tweet_entry['char_count']}",
        f"- 发布状态: {'✅' if tweet_entry['published'] else '❌'}",
    ]
    if tweet_entry.get("tweet_id"):
        new_tweet_lines.append(f"- Tweet ID: {tweet_entry['tweet_id']}")
    new_tweet_lines.append("")

    # 插入新推文
    lines[insert_idx:insert_idx] = new_tweet_lines
    md_file.write_text("\n".join(lines), encoding="utf-8")
    logger.info("增量更新 Markdown（推文 %d）: %s", tweet_num, md_file)

    # 同步到目标目录
    sync_to_target(md_file)

    return md_file
