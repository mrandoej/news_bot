import asyncio
import logging
from typing import List, Optional, Tuple
from telegram import Bot
from telegram.error import TelegramError

from core.interfaces import INotificationService, NewsItem
from core.circuit_breaker import CircuitBreaker
from core.retry import smart_retry
from core.metrics import timed_metric, IMetricsCollector
from infrastructure.config_manager import TelegramConfig


class TelegramNotificationService(INotificationService):
    def __init__(self, config: TelegramConfig, metrics: IMetricsCollector):
        self.config = config
        self.metrics = metrics
        self.logger = logging.getLogger(__name__)
        self.bot = Bot(token=config.bot_token)
        self.circuit_breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=300.0)
    
    @timed_metric(lambda self: self.metrics, "notification.send_news")
    async def send_news(self, news: NewsItem, content: str) -> Optional[int]:
        try:
            return await self.circuit_breaker.call(self._send_news_internal, news, content)
        except Exception as e:
            self.logger.error(f"Failed to send news {news.id}: {e}")
            self.metrics.increment_counter("notification.send_error", {"source": news.source})
            return None
    
    @smart_retry(max_attempts=3, base_delay=2.0, exceptions=(TelegramError,))
    async def _send_news_internal(self, news: NewsItem, content: str) -> Optional[int]:
        try:
            message = self._format_news_message(news, content)
            
            sent_message = await self.bot.send_message(
                chat_id=self.config.channel_id,
                text=message,
                parse_mode='HTML',
                disable_web_page_preview=False
            )
            
            self.metrics.increment_counter("notification.send_success", {"source": news.source})
            self.logger.info(f"News sent to channel: {news.title[:50]}...")
            return sent_message.message_id
            
        except TelegramError as e:
            self.metrics.increment_counter("notification.telegram_error", {"source": news.source})
            self.logger.error(f"Telegram error sending news: {e}")
            raise
        except Exception as e:
            self.metrics.increment_counter("notification.send_error", {"source": news.source})
            self.logger.error(f"Unexpected error sending news: {e}")
            return None
    
    @timed_metric(lambda self: self.metrics, "notification.send_multiple")
    async def send_multiple_news(self, news_items: List[Tuple[NewsItem, str]]) -> List[Tuple[int, Optional[int], bool]]:
        results = []
        
        for news, content in news_items:
            try:
                message_id = await self.send_news(news, content)
                success = message_id is not None
                results.append((news.id, message_id, success))
                
                if success:
                    # Delay between sends to respect rate limits
                    await asyncio.sleep(self.config.delay_seconds)
                
            except Exception as e:
                self.logger.error(f"Error sending news {news.id}: {e}")
                results.append((news.id, None, False))
        
        successful_sends = sum(1 for _, _, success in results if success)
        self.metrics.set_gauge("notification.batch_success_rate", 
                              successful_sends / len(results) if results else 0)
        
        return results
    
    @timed_metric(lambda self: self.metrics, "notification.check_availability")
    async def is_available(self) -> bool:
        try:
            bot_info = await self.bot.get_me()
            self.logger.debug(f"Telegram connection successful. Bot: {bot_info.username}")
            
            # Check channel access
            try:
                chat_info = await self.bot.get_chat(self.config.channel_id)
                self.logger.debug(f"Channel access confirmed: {chat_info.title}")
                self.metrics.increment_counter("notification.availability_success")
                return True
            except TelegramError as e:
                self.logger.error(f"Channel access error {self.config.channel_id}: {e}")
                self.metrics.increment_counter("notification.channel_access_error")
                return False
                
        except TelegramError as e:
            self.logger.error(f"Telegram connection error: {e}")
            self.metrics.increment_counter("notification.connection_error")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error checking Telegram availability: {e}")
            self.metrics.increment_counter("notification.availability_error")
            return False
    
    def _format_news_message(self, news: NewsItem, rephrased_content: str) -> str:
        try:
            title, content = self._extract_title_and_content(rephrased_content)
            
            if not title:
                title = news.title
            if not content:
                content = rephrased_content
            
            # Format message without source attribution
            message = content
            
            # Add original link if available
            if news.url:
                message += f"\n\n🔗 <a href=\"{news.url}\">Читать оригинал</a>"
            
            # Limit message length (Telegram limit ~4096 characters)
            if len(message) > self.config.message_limit:
                content_limit = self.config.message_limit - 100
                content = content[:content_limit] + "..."
                
                message = content
                if news.url:
                    message += f"\n\n🔗 <a href=\"{news.url}\">Читать полностью</a>"
            
            return message
            
        except Exception as e:
            self.logger.error(f"Error formatting message: {e}")
            # Return basic message on error
            return f"📰 <b>{news.title}</b>\n\n{rephrased_content[:3000]}..."
    
    def _extract_title_and_content(self, rephrased_text: str) -> Tuple[str, str]:
        try:
            lines = rephrased_text.strip().split('\n')
            title = ""
            content = ""
            
            for i, line in enumerate(lines):
                line = line.strip()
                if line.startswith('Заголовок:'):
                    title = line.replace('Заголовок:', '').strip()
                elif line.startswith('Текст:'):
                    content = line.replace('Текст:', '').strip()
                    # Add all subsequent lines to content
                    if i + 1 < len(lines):
                        remaining_lines = [l.strip() for l in lines[i+1:] if l.strip()]
                        if remaining_lines:
                            content += ' ' + ' '.join(remaining_lines)
                    break
            
            # If structure not found, try to determine automatically
            if not title and not content:
                if lines:
                    # First line as title, rest as content
                    title = lines[0].strip()
                    if len(lines) > 1:
                        content = ' '.join([l.strip() for l in lines[1:] if l.strip()])
                    else:
                        content = title
                        title = "Новость"
            
            return title, content
            
        except Exception as e:
            self.logger.error(f"Error extracting title and content: {e}")
            return "", rephrased_text
    
    async def send_status_message(self, message: str) -> bool:
        try:
            await self.bot.send_message(
                chat_id=self.config.channel_id,
                text=f"🤖 <b>Статус бота:</b>\n{message}",
                parse_mode='HTML'
            )
            self.metrics.increment_counter("notification.status_message_sent")
            return True
        except Exception as e:
            self.logger.error(f"Error sending status message: {e}")
            self.metrics.increment_counter("notification.status_message_error")
            return False