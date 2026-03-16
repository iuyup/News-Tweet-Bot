"""
LLM 推文生成
调用 Claude 或 MiniMax API，返回结构化推文列表
"""
import asyncio
import json
import logging
from dataclasses import dataclass

import anthropic
import httpx

from src.config import settings
from src.models.news_item import Category, NewsItem
from src.prompts import build_tweet_prompt

logger = logging.getLogger(__name__)


@dataclass
class Usage:
    """兼容的 usage 对象"""
    input_tokens: int
    output_tokens: int

# 每次 API 调用之间的指数退避参数
_MAX_RETRIES = 3
_BASE_DELAY = 2.0


class TweetGenerationError(Exception):
    pass


async def _call_claude(prompt: str) -> tuple[str, anthropic.types.Usage]:
    """向 Claude 发送单次请求，返回 (raw_text, usage)"""
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    for attempt in range(_MAX_RETRIES):
        try:
            response = await client.messages.create(
                model=settings.claude_model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            # 处理可能的 ThinkingBlock（扩展思考）
            text_parts = []
            for block in response.content:
                if hasattr(block, "text"):
                    text_parts.append(block.text)
                elif hasattr(block, "thinking"):
                    logger.debug("Claude 扩展思考: %s", block.thinking[:100])
            raw_text = "".join(text_parts)
            return raw_text, response.usage
        except anthropic.RateLimitError as e:
            delay = _BASE_DELAY ** (attempt + 1)
            logger.warning("Rate limit，%.1f 秒后重试（%d/%d）", delay, attempt + 1, _MAX_RETRIES)
            await asyncio.sleep(delay)
        except anthropic.APIError as e:
            if attempt == _MAX_RETRIES - 1:
                raise TweetGenerationError(f"Claude API 调用失败: {e}") from e
            await asyncio.sleep(_BASE_DELAY)

    raise TweetGenerationError("超过最大重试次数")


async def _call_minimax(prompt: str) -> tuple[str, Usage]:
    """向 MiniMax 发送单次请求，返回 (raw_text, usage)"""
    if not settings.minimax_api_key:
        raise TweetGenerationError("MiniMax API 密钥未配置")

    url = "https://api.minimax.chat/v1/text/chatcompletion_v2"
    headers = {
        "Authorization": f"Bearer {settings.minimax_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.minimax_model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1024,
    }

    for attempt in range(_MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(url, headers=headers, json=payload)
                if response.status_code == 429:
                    delay = _BASE_DELAY ** (attempt + 1)
                    logger.warning("MiniMax Rate limit，%.1f 秒后重试（%d/%d）", delay, attempt + 1, _MAX_RETRIES)
                    await asyncio.sleep(delay)
                    continue
                if response.status_code != 200:
                    if attempt == _MAX_RETRIES - 1:
                        raise TweetGenerationError(f"MiniMax API 错误: {response.status_code} {response.text}")
                    await asyncio.sleep(_BASE_DELAY)
                    continue

                data = response.json()
                raw_text = data["choices"][0]["message"]["content"]

                # MiniMax 返回的 usage 格式
                usage_data = data.get("usage", {})
                input_tokens = usage_data.get("prompt_tokens", 0)
                output_tokens = usage_data.get("completion_tokens", 0)

                return raw_text, Usage(input_tokens=input_tokens, output_tokens=output_tokens)

        except httpx.TimeoutException:
            if attempt == _MAX_RETRIES - 1:
                raise TweetGenerationError("MiniMax API 请求超时")
            await asyncio.sleep(_BASE_DELAY)
        except httpx.HTTPError as e:
            if attempt == _MAX_RETRIES - 1:
                raise TweetGenerationError(f"MiniMax API 调用失败: {e}") from e
            await asyncio.sleep(_BASE_DELAY)

    raise TweetGenerationError("超过最大重试次数")


async def _call_deepseek(prompt: str) -> tuple[str, Usage]:
    """向 DeepSeek 发送单次请求，返回 (raw_text, usage)"""
    if not settings.deepseek_api_key:
        raise TweetGenerationError("DeepSeek API 密钥未配置")

    # 使用 OpenAI 兼容格式
    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.deepseek_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.deepseek_model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1024,
    }

    for attempt in range(_MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(url, headers=headers, json=payload)
                if response.status_code == 429:
                    delay = _BASE_DELAY ** (attempt + 1)
                    logger.warning("DeepSeek Rate limit，%.1f 秒后重试（%d/%d）", delay, attempt + 1, _MAX_RETRIES)
                    await asyncio.sleep(delay)
                    continue
                if response.status_code != 200:
                    if attempt == _MAX_RETRIES - 1:
                        raise TweetGenerationError(f"DeepSeek API 错误: {response.status_code} {response.text}")
                    await asyncio.sleep(_BASE_DELAY)
                    continue

                data = response.json()
                raw_text = data["choices"][0]["message"]["content"]

                # DeepSeek 返回的 usage 格式
                usage_data = data.get("usage", {})
                input_tokens = usage_data.get("prompt_tokens", 0)
                output_tokens = usage_data.get("completion_tokens", 0)

                return raw_text, Usage(input_tokens=input_tokens, output_tokens=output_tokens)

        except httpx.TimeoutException:
            if attempt == _MAX_RETRIES - 1:
                raise TweetGenerationError("DeepSeek API 请求超时")
            await asyncio.sleep(_BASE_DELAY)
        except httpx.HTTPError as e:
            if attempt == _MAX_RETRIES - 1:
                raise TweetGenerationError(f"DeepSeek API 调用失败: {e}") from e
            await asyncio.sleep(_BASE_DELAY)

    raise TweetGenerationError("超过最大重试次数")


def _parse_response(raw: str) -> dict:
    """从 Claude 响应中提取 JSON"""
    # 处理 Claude 有时会在 JSON 外包裹 markdown 代码块的情况
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1])
    return json.loads(text)


async def generate_tweets(
    items: list[NewsItem],
    count: int | None = None,
) -> list[dict]:
    """
    从新闻条目中生成推文。
    按分类分别调用 Claude，返回推文字典列表：
    [{"tweet": str, "news_item": NewsItem, "input_tokens": int, "output_tokens": int}]
    """
    count = count or settings.tweets_per_run
    results: list[dict] = []

    # 按分类分组
    by_category: dict[Category, list[NewsItem]] = {
        Category.POLITICS: [],
        Category.TECH: [],
    }
    for item in items:
        if item.category in by_category:
            by_category[item.category].append(item)

    for category, cat_items in by_category.items():
        if not cat_items or len(results) >= count:
            break

        # 每个分类取热度最高的 10 条给 Claude 选
        top_items = cat_items[:10]
        prompt = build_tweet_prompt(top_items, category)

        try:
            # 根据配置选择 LLM 提供商
            if settings.default_llm_provider == "minimax":
                raw, usage = await _call_minimax(prompt)
            elif settings.default_llm_provider == "deepseek":
                raw, usage = await _call_deepseek(prompt)
            else:
                raw, usage = await _call_claude(prompt)
            data = _parse_response(raw)
            tweet_text: str = data["tweet"]
            source_idx: int = data["source_index"] - 1  # 转为 0-based

            if len(tweet_text) > settings.tweet_max_length:
                logger.warning("推文超长 %d 字符，截断", len(tweet_text))
                tweet_text = tweet_text[:settings.tweet_max_length]

            chosen_item = top_items[source_idx] if 0 <= source_idx < len(top_items) else top_items[0]

            results.append({
                "tweet": tweet_text,
                "news_item": chosen_item,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
            })
            logger.info(
                "[%s] 生成推文 (%d chars) | tokens in=%d out=%d",
                category.value,
                len(tweet_text),
                usage.input_tokens,
                usage.output_tokens,
            )
        except (TweetGenerationError, json.JSONDecodeError, KeyError, IndexError) as e:
            logger.error("生成推文失败 [%s]: %s", category.value, e)

    return results
