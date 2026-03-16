"""
抓取模块测试
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from src.scrapers.reddit_scraper import fetch_subreddit
from src.models.news_item import Category


class TestRedditScraper:
    """Reddit 抓取器测试"""

    @pytest.mark.asyncio
    async def test_fetch_subreddit_success(self):
        """测试成功抓取 Reddit 子版"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "children": [
                    {
                        "data": {
                            "title": "Test Post Title",
                            "url": "https://example.com/post",
                            "permalink": "/r/technology/comments/abc123",
                            "score": 100,
                            "stickied": False,
                            "is_video": False,
                        }
                    }
                ]
            }
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        items, error = await fetch_subreddit(mock_client, "technology", Category.TECH, 10)

        assert error is None
        assert len(items) == 1
        assert items[0].title == "Test Post Title"
        assert items[0].source == "reddit/r/technology"

    @pytest.mark.asyncio
    async def test_fetch_subreddit_skip_stickied(self):
        """测试跳过置顶帖"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "children": [
                    {
                        "data": {
                            "title": "Stickied Post",
                            "url": "https://example.com/post",
                            "permalink": "/r/technology/comments/abc123",
                            "score": 100,
                            "stickied": True,  # 置顶帖应跳过
                            "is_video": False,
                        }
                    },
                    {
                        "data": {
                            "title": "Normal Post",
                            "url": "https://example.com/post2",
                            "permalink": "/r/technology/comments/def456",
                            "score": 50,
                            "stickied": False,
                            "is_video": False,
                        }
                    }
                ]
            }
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        items, error = await fetch_subreddit(mock_client, "technology", Category.TECH, 10)

        assert len(items) == 1
        assert items[0].title == "Normal Post"

    @pytest.mark.asyncio
    async def test_fetch_subreddit_rate_limit(self):
        """测试 API 限流处理"""
        from httpx import HTTPStatusError

        mock_response = MagicMock()
        mock_response.status_code = 429

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=HTTPStatusError(
                "Rate Limited",
                request=MagicMock(),
                response=mock_response
            )
        )

        items, error = await fetch_subreddit(mock_client, "technology", Category.TECH, 10)

        assert len(items) == 0
        assert "限流" in error or "429" in error
