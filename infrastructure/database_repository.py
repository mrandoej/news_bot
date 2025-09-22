import aiosqlite
import hashlib
import logging
from datetime import datetime
from typing import List, Dict, Optional, Any
from contextlib import asynccontextmanager
import os

from core.interfaces import INewsRepository, NewsItem, NewsStatus
from infrastructure.config_manager import DatabaseConfig
from core.metrics import timed_metric, IMetricsCollector


class AsyncNewsRepository(INewsRepository):
    def __init__(self, config: DatabaseConfig, metrics: IMetricsCollector):
        self.config = config
        self.metrics = metrics
        self.logger = logging.getLogger(__name__)
        self._initialized = False
    
    async def initialize(self):
        if not self._initialized:
            await self._init_database()
            self._initialized = True
    
    async def _init_database(self):
        # Create directory if it doesn't exist
        db_dir = os.path.dirname(self.config.path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        
        async with aiosqlite.connect(self.config.path) as db:
            # Create table if not exists
            await db.execute('''
                CREATE TABLE IF NOT EXISTS news (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    url TEXT,
                    source TEXT NOT NULL,
                    city TEXT,
                    original_hash TEXT UNIQUE NOT NULL,
                    rephrased_content TEXT,
                    published_date DATETIME,
                    created_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'parsed',
                    telegram_message_id INTEGER
                )
            ''')
            
            # Check if status column exists and add it if not (migration)
            cursor = await db.execute("PRAGMA table_info(news)")
            columns = await cursor.fetchall()
            column_names = [column[1] for column in columns]
            
            if 'status' not in column_names:
                self.logger.info("Adding status column to existing database")
                await db.execute('ALTER TABLE news ADD COLUMN status TEXT DEFAULT "parsed"')
                
                # Update existing records based on their current state
                await db.execute('''
                    UPDATE news SET status = CASE
                        WHEN sent_to_telegram = 1 THEN 'sent'
                        WHEN rephrased_content IS NOT NULL THEN 'processed'
                        ELSE 'parsed'
                    END
                ''')
            
            # Create indexes
            await db.execute('CREATE INDEX IF NOT EXISTS idx_url ON news(url)')
            await db.execute('CREATE INDEX IF NOT EXISTS idx_hash ON news(original_hash)')
            await db.execute('CREATE INDEX IF NOT EXISTS idx_status ON news(status)')
            await db.execute('CREATE INDEX IF NOT EXISTS idx_source ON news(source)')
            await db.execute('CREATE INDEX IF NOT EXISTS idx_created_date ON news(created_date)')
            
            await db.commit()
            
        self.logger.info("Database initialized successfully")
    
    @asynccontextmanager
    async def _get_connection(self):
        await self.initialize()
        async with aiosqlite.connect(self.config.path) as db:
            db.row_factory = aiosqlite.Row
            yield db
    
    def _generate_content_hash(self, title: str, content: str) -> str:
        combined = f"{title.strip()}{content.strip()}"
        return hashlib.sha256(combined.encode('utf-8')).hexdigest()
    
    @timed_metric(lambda self: self.metrics, "repository.save_news")
    async def save_news(self, news: NewsItem) -> Optional[int]:
        try:
            if await self.news_exists(news):
                self.logger.debug(f"News already exists: {news.title[:50]}...")
                return None
            
            content_hash = self._generate_content_hash(news.title, news.content)
            
            async with self._get_connection() as db:
                cursor = await db.execute('''
                    INSERT INTO news (title, content, url, source, city, 
                                    original_hash, published_date, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    news.title, news.content, news.url, news.source, 
                    news.city, content_hash, news.published_date, news.status.value
                ))
                
                news_id = cursor.lastrowid
                await db.commit()
                
                self.metrics.increment_counter("repository.news_saved", {"source": news.source})
                self.logger.info(f"News saved with ID {news_id}: {news.title[:50]}...")
                return news_id
                
        except Exception as e:
            self.metrics.increment_counter("repository.save_error", {"source": news.source})
            self.logger.error(f"Error saving news: {e}")
            return None
    
    @timed_metric(lambda self: self.metrics, "repository.get_news_by_status")
    async def get_news_by_status(self, status: NewsStatus, limit: int = 10) -> List[NewsItem]:
        async with self._get_connection() as db:
            cursor = await db.execute('''
                SELECT * FROM news 
                WHERE status = ? 
                ORDER BY created_date ASC 
                LIMIT ?
            ''', (status.value, limit))
            
            rows = await cursor.fetchall()
            return [self._row_to_news_item(row) for row in rows]
    
    @timed_metric(lambda self: self.metrics, "repository.update_news_status")
    async def update_news_status(self, news_id: int, status: NewsStatus, **kwargs) -> bool:
        try:
            async with self._get_connection() as db:
                # Build update query dynamically based on kwargs
                update_fields = ["status = ?"]
                params = [status.value]
                
                if 'rephrased_content' in kwargs:
                    update_fields.append("rephrased_content = ?")
                    params.append(kwargs['rephrased_content'])
                
                if 'telegram_message_id' in kwargs:
                    update_fields.append("telegram_message_id = ?")
                    params.append(kwargs['telegram_message_id'])
                
                params.append(news_id)
                
                query = f"UPDATE news SET {', '.join(update_fields)} WHERE id = ?"
                
                cursor = await db.execute(query, params)
                await db.commit()
                
                if cursor.rowcount > 0:
                    self.metrics.increment_counter("repository.status_updated", {"status": status.value})
                    self.logger.debug(f"News {news_id} status updated to {status.value}")
                    return True
                else:
                    self.logger.warning(f"News {news_id} not found for status update")
                    return False
                    
        except Exception as e:
            self.metrics.increment_counter("repository.update_error")
            self.logger.error(f"Error updating news status: {e}")
            return False
    
    @timed_metric(lambda self: self.metrics, "repository.news_exists")
    async def news_exists(self, news: NewsItem) -> bool:
        content_hash = self._generate_content_hash(news.title, news.content)
        
        async with self._get_connection() as db:
            # Check by hash
            cursor = await db.execute('SELECT id FROM news WHERE original_hash = ?', (content_hash,))
            if await cursor.fetchone():
                return True
            
            # Check by URL if available
            if news.url:
                cursor = await db.execute('SELECT id FROM news WHERE url = ?', (news.url,))
                if await cursor.fetchone():
                    return True
            
            return False
    
    @timed_metric(lambda self: self.metrics, "repository.get_statistics")
    async def get_statistics(self) -> Dict[str, Any]:
        async with self._get_connection() as db:
            stats = {}
            
            # Total news count
            cursor = await db.execute('SELECT COUNT(*) FROM news')
            row = await cursor.fetchone()
            stats['total_news'] = row[0]
            
            # Count by status
            cursor = await db.execute('''
                SELECT status, COUNT(*) as count 
                FROM news 
                GROUP BY status
            ''')
            rows = await cursor.fetchall()
            stats['by_status'] = {row[0]: row[1] for row in rows}
            
            # Count by source
            cursor = await db.execute('''
                SELECT source, COUNT(*) as count 
                FROM news 
                GROUP BY source 
                ORDER BY count DESC
            ''')
            rows = await cursor.fetchall()
            stats['by_source'] = {row[0]: row[1] for row in rows}
            
            # Count by city
            cursor = await db.execute('''
                SELECT city, COUNT(*) as count 
                FROM news 
                WHERE city IS NOT NULL 
                GROUP BY city 
                ORDER BY count DESC
            ''')
            rows = await cursor.fetchall()
            stats['by_city'] = {row[0]: row[1] for row in rows}
            
            # Recent activity (last 24 hours)
            cursor = await db.execute('''
                SELECT COUNT(*) FROM news 
                WHERE created_date >= datetime('now', '-1 day')
            ''')
            row = await cursor.fetchone()
            stats['last_24h'] = row[0]
            
            return stats
    
    @timed_metric(lambda self: self.metrics, "repository.cleanup_old_news")
    async def cleanup_old_news(self, days: int) -> int:
        try:
            async with self._get_connection() as db:
                cursor = await db.execute('''
                    DELETE FROM news 
                    WHERE created_date < datetime('now', '-' || ? || ' days')
                    AND status = 'sent'
                ''', (days,))
                
                deleted_count = cursor.rowcount
                await db.commit()
                
                self.metrics.increment_counter("repository.news_cleaned")
                self.metrics.set_gauge("repository.cleaned_count", deleted_count)
                self.logger.info(f"Cleaned up {deleted_count} old news items")
                return deleted_count
                
        except Exception as e:
            self.metrics.increment_counter("repository.cleanup_error")
            self.logger.error(f"Error cleaning up old news: {e}")
            return 0
    
    def _row_to_news_item(self, row) -> NewsItem:
        return NewsItem(
            id=row['id'],
            title=row['title'],
            content=row['content'],
            url=row['url'],
            source=row['source'],
            city=row['city'],
            published_date=datetime.fromisoformat(row['published_date']) if row['published_date'] else None,
            created_date=datetime.fromisoformat(row['created_date']) if row['created_date'] else None,
            status=NewsStatus(row['status']),
            rephrased_content=row['rephrased_content'],
            telegram_message_id=row['telegram_message_id']
        )