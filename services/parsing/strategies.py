import aiohttp
import asyncio
import feedparser
import logging
import re
from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional
from bs4 import BeautifulSoup
from urllib.parse import urljoin

from core.interfaces import NewsItem, SourceConfig
from core.retry import smart_retry
from core.metrics import timed_metric, IMetricsCollector


class IParsingStrategy(ABC):
    @abstractmethod
    async def parse(self, source: SourceConfig, session: aiohttp.ClientSession) -> List[NewsItem]:
        pass


class RSSParsingStrategy(IParsingStrategy):
    def __init__(self, metrics: IMetricsCollector, max_items: int = 10):
        self.metrics = metrics
        self.max_items = max_items
        self.logger = logging.getLogger(__name__)
    
    @timed_metric(lambda self: self.metrics, "parsing.rss")
    @smart_retry(max_attempts=3, exceptions=(aiohttp.ClientError, asyncio.TimeoutError))
    async def parse(self, source: SourceConfig, session: aiohttp.ClientSession) -> List[NewsItem]:
        if not source.rss:
            return []
        
        try:
            self.logger.debug(f"Parsing RSS: {source.name} - {source.rss}")
            
            async with session.get(source.rss, timeout=aiohttp.ClientTimeout(total=source.timeout)) as response:
                response.raise_for_status()
                content = await response.text()
            
            # Parse RSS feed
            feed = feedparser.parse(content)
            
            if feed.bozo:
                self.logger.warning(f"RSS feed may contain errors: {source.name}")
            
            news_items = []
            for entry in feed.entries[:self.max_items]:
                try:
                    news_item = await self._parse_rss_entry(entry, source)
                    if news_item:
                        news_items.append(news_item)
                except Exception as e:
                    self.logger.error(f"Error parsing RSS entry: {e}")
                    continue
            
            self.metrics.increment_counter("parsing.rss.success", {"source": source.name})
            self.logger.info(f"Parsed {len(news_items)} items from RSS {source.name}")
            return news_items
            
        except Exception as e:
            self.metrics.increment_counter("parsing.rss.error", {"source": source.name})
            self.logger.error(f"Error parsing RSS {source.name}: {e}")
            return []
    
    async def _parse_rss_entry(self, entry, source: SourceConfig) -> Optional[NewsItem]:
        title = self._clean_text(getattr(entry, 'title', ''))
        content = self._clean_text(
            getattr(entry, 'description', '') or 
            getattr(entry, 'summary', '')
        )
        url = getattr(entry, 'link', '')
        
        if not title or not content:
            return None
        
        # Extract publication date
        published_date = None
        if hasattr(entry, 'published_parsed') and entry.published_parsed:
            try:
                published_date = datetime(*entry.published_parsed[:6])
            except (ValueError, TypeError):
                pass
        elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
            try:
                published_date = datetime(*entry.updated_parsed[:6])
            except (ValueError, TypeError):
                pass
        
        if not published_date:
            published_date = datetime.now()
        
        return NewsItem(
            title=title,
            content=content,
            url=url,
            source=source.name,
            city=source.city,
            published_date=published_date
        )
    
    def _clean_text(self, text: str) -> str:
        if not text:
            return ""
        
        # Remove HTML tags
        text = re.sub(r'<[^>]+>', '', text)
        
        # Normalize whitespace
        text = re.sub(r'\s+', ' ', text)
        
        # Remove special characters but keep basic punctuation
        text = re.sub(r'[^\w\s\-.,!?:;()«»""\']', ' ', text)
        text = re.sub(r'\s+', ' ', text)
        
        return text.strip()


class HTMLParsingStrategy(IParsingStrategy):
    def __init__(self, metrics: IMetricsCollector, max_items: int = 10, min_content_length: int = 100):
        self.metrics = metrics
        self.max_items = max_items
        self.min_content_length = min_content_length
        self.logger = logging.getLogger(__name__)
    
    @timed_metric(lambda self: self.metrics, "parsing.html")
    @smart_retry(max_attempts=3, exceptions=(aiohttp.ClientError, asyncio.TimeoutError))
    async def parse(self, source: SourceConfig, session: aiohttp.ClientSession) -> List[NewsItem]:
        if not source.selector:
            return []
        
        try:
            self.logger.debug(f"Parsing HTML: {source.name} - {source.url}")
            
            async with session.get(source.url, timeout=aiohttp.ClientTimeout(total=source.timeout)) as response:
                response.raise_for_status()
                content = await response.text()
            
            soup = BeautifulSoup(content, 'html.parser')
            news_blocks = soup.select(source.selector)
            
            news_items = []
            for block in news_blocks[:self.max_items]:
                try:
                    news_item = await self._parse_html_block(block, source, session)
                    if news_item:
                        news_items.append(news_item)
                except Exception as e:
                    self.logger.error(f"Error parsing HTML block: {e}")
                    continue
            
            self.metrics.increment_counter("parsing.html.success", {"source": source.name})
            self.logger.info(f"Parsed {len(news_items)} items from HTML {source.name}")
            return news_items
            
        except Exception as e:
            self.metrics.increment_counter("parsing.html.error", {"source": source.name})
            self.logger.error(f"Error parsing HTML {source.name}: {e}")
            return []
    
    async def _parse_html_block(self, block, source: SourceConfig, session: aiohttp.ClientSession) -> Optional[NewsItem]:
        # Extract title
        title_elem = block.find(['h1', 'h2', 'h3', 'h4', 'a'])
        if not title_elem:
            return None
        
        title = self._clean_text(title_elem.get_text())
        if not title or len(title) < 10:
            return None
        
        # Extract URL
        link_elem = block.find('a')
        url = ""
        if link_elem and link_elem.get('href'):
            url = urljoin(source.url, link_elem['href'])
        
        # Extract content
        content_elem = block.find(['p', 'div', 'span'])
        content = ""
        if content_elem:
            content = self._clean_text(content_elem.get_text())
        
        # If content is too short, try to get full article
        if len(content) < self.min_content_length and url:
            full_content = await self._get_full_article_content(url, session)
            if full_content:
                content = full_content
        
        if not content or len(content) < 20:
            return None
        
        # Extract date
        date_elem = block.find(['time', '.date', '.time'])
        published_date = datetime.now()
        if date_elem:
            date_text = date_elem.get_text() or date_elem.get('datetime', '')
            published_date = self._extract_date_from_text(date_text) or datetime.now()
        
        return NewsItem(
            title=title,
            content=content,
            url=url,
            source=source.name,
            city=source.city,
            published_date=published_date
        )
    
    async def _get_full_article_content(self, url: str, session: aiohttp.ClientSession) -> Optional[str]:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as response:
                response.raise_for_status()
                content = await response.text()
            
            soup = BeautifulSoup(content, 'html.parser')
            
            # Remove unwanted elements
            for element in soup(['script', 'style', 'nav', 'header', 'footer', 'aside']):
                element.decompose()
            
            # Try to find main content
            content_selectors = [
                'article', '.article', '.content', '.post-content',
                '.entry-content', '.news-content', '.text', '.body'
            ]
            
            content_text = ""
            for selector in content_selectors:
                content_elem = soup.select_one(selector)
                if content_elem:
                    content_text = self._clean_text(content_elem.get_text())
                    break
            
            # Fallback to all paragraphs
            if not content_text:
                paragraphs = soup.find_all('p')
                content_text = ' '.join([self._clean_text(p.get_text()) for p in paragraphs])
            
            # Limit content length
            if len(content_text) > 2000:
                content_text = content_text[:2000] + "..."
            
            return content_text if len(content_text) > 50 else None
            
        except Exception as e:
            self.logger.debug(f"Error getting full article content: {e}")
            return None
    
    def _extract_date_from_text(self, text: str) -> Optional[datetime]:
        try:
            date_patterns = [
                r'(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+(\d{4})',
                r'(\d{1,2})\.(\d{1,2})\.(\d{4})',
                r'(\d{4})-(\d{1,2})-(\d{1,2})'
            ]
            
            months = {
                'января': 1, 'февраля': 2, 'марта': 3, 'апреля': 4,
                'мая': 5, 'июня': 6, 'июля': 7, 'августа': 8,
                'сентября': 9, 'октября': 10, 'ноября': 11, 'декабря': 12
            }
            
            for pattern in date_patterns:
                match = re.search(pattern, text.lower())
                if match:
                    if 'января' in pattern:
                        day, month_name, year = match.groups()
                        month = months.get(month_name)
                        if month:
                            return datetime(int(year), month, int(day))
                    else:
                        groups = match.groups()
                        if len(groups) == 3:
                            if '-' in pattern:
                                year, month, day = groups
                            else:
                                day, month, year = groups
                            return datetime(int(year), int(month), int(day))
            
            return None
            
        except Exception:
            return None
    
    def _clean_text(self, text: str) -> str:
        if not text:
            return ""
        
        # Remove HTML tags
        text = re.sub(r'<[^>]+>', '', text)
        
        # Normalize whitespace
        text = re.sub(r'\s+', ' ', text)
        
        # Remove special characters but keep basic punctuation
        text = re.sub(r'[^\w\s\-.,!?:;()«»""\']', ' ', text)
        text = re.sub(r'\s+', ' ', text)
        
        return text.strip()


class ParsingStrategyFactory:
    @staticmethod
    def create_strategy(source: SourceConfig, metrics: IMetricsCollector, max_items: int = 10) -> IParsingStrategy:
        if source.rss:
            return RSSParsingStrategy(metrics, max_items)
        elif source.selector:
            return HTMLParsingStrategy(metrics, max_items)
        else:
            raise ValueError(f"No parsing strategy available for source: {source.name}")