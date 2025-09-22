import asyncio
import logging
from datetime import datetime
from typing import List, Dict, Any

from core.interfaces import (
    INewsParser, INewsRepository, IContentProcessor, INotificationService, 
    IHealthChecker, IMetricsCollector, IEventBus, NewsStatus, ProcessingResult
)
from core.metrics import timed_metric
from infrastructure.config_manager import AppConfig


class NewsBotService:
    def __init__(
        self,
        config: AppConfig,
        parser: INewsParser,
        repository: INewsRepository,
        content_processor: IContentProcessor,
        notification_service: INotificationService,
        health_checker: IHealthChecker,
        metrics: IMetricsCollector,
        event_bus: IEventBus
    ):
        self.config = config
        self.parser = parser
        self.repository = repository
        self.content_processor = content_processor
        self.notification_service = notification_service
        self.health_checker = health_checker
        self.metrics = metrics
        self.event_bus = event_bus
        self.logger = logging.getLogger(__name__)
        
        # Subscribe to events
        self._setup_event_handlers()
    
    def _setup_event_handlers(self):
        self.event_bus.subscribe('news.parsed', self._on_news_parsed)
        self.event_bus.subscribe('news.processed', self._on_news_processed)
        self.event_bus.subscribe('news.sent', self._on_news_sent)
        self.event_bus.subscribe('error.occurred', self._on_error_occurred)
    
    @timed_metric(lambda self: self.metrics, "bot.full_cycle")
    async def run_full_cycle(self) -> ProcessingResult:
        self.logger.info("=" * 50)
        self.logger.info("Starting full news processing cycle")
        self.logger.info("=" * 50)
        
        start_time = asyncio.get_event_loop().time()
        errors = []
        
        try:
            # 1. Health check
            health_status = await self.health_checker.check_health()
            if not health_status.get('overall', False):
                error_msg = "Health check failed, skipping cycle"
                self.logger.error(error_msg)
                await self.event_bus.publish('error.occurred', {
                    'error': error_msg,
                    'health_status': health_status
                })
                return ProcessingResult(
                    success=False,
                    processed_count=0,
                    failed_count=0,
                    errors=[error_msg],
                    duration=asyncio.get_event_loop().time() - start_time
                )
            
            # 2. Parse and save news
            parsed_count = await self._parse_and_save_news()
            
            # 3. Process content
            processed_count = await self._process_news_content()
            
            # 4. Send notifications
            sent_count = await self._send_notifications()
            
            # 5. Cleanup old news if needed
            cleanup_count = 0
            current_hour = datetime.now().hour
            if current_hour == self.config.cleanup_hour:
                cleanup_count = await self.repository.cleanup_old_news(days=7)
                self.logger.info(f"Cleaned up {cleanup_count} old news items")
            
            # 6. Get final statistics
            stats = await self.repository.get_statistics()
            
            duration = asyncio.get_event_loop().time() - start_time
            
            self.logger.info("Cycle completed successfully:")
            self.logger.info(f"  - Parsed new news: {parsed_count}")
            self.logger.info(f"  - Processed content: {processed_count}")
            self.logger.info(f"  - Sent notifications: {sent_count}")
            self.logger.info(f"  - Total news in database: {stats.get('total_news', 0)}")
            self.logger.info(f"  - Cycle duration: {duration:.2f}s")
            
            if cleanup_count > 0:
                self.logger.info(f"  - Cleaned up old news: {cleanup_count}")
            
            # Update metrics
            self.metrics.set_gauge("bot.cycle_duration", duration)
            self.metrics.set_gauge("bot.parsed_count", parsed_count)
            self.metrics.set_gauge("bot.processed_count", processed_count)
            self.metrics.set_gauge("bot.sent_count", sent_count)
            self.metrics.increment_counter("bot.cycle_completed")
            
            await self.event_bus.publish('cycle.completed', {
                'parsed_count': parsed_count,
                'processed_count': processed_count,
                'sent_count': sent_count,
                'duration': duration,
                'statistics': stats
            })
            
            return ProcessingResult(
                success=True,
                processed_count=parsed_count + processed_count + sent_count,
                failed_count=0,
                errors=errors,
                duration=duration
            )
            
        except Exception as e:
            duration = asyncio.get_event_loop().time() - start_time
            error_msg = f"Critical error in full cycle: {e}"
            self.logger.error(error_msg)
            errors.append(error_msg)
            
            self.metrics.increment_counter("bot.cycle_failed")
            await self.event_bus.publish('error.occurred', {
                'error': error_msg,
                'exception': str(e)
            })
            
            return ProcessingResult(
                success=False,
                processed_count=0,
                failed_count=1,
                errors=errors,
                duration=duration
            )
    
    @timed_metric(lambda self: self.metrics, "bot.parse_and_save")
    async def _parse_and_save_news(self) -> int:
        self.logger.info("Starting news parsing and saving")
        
        try:
            # Parse news from all sources
            news_items = await self.parser.parse_all_sources()
            
            if not news_items:
                self.logger.warning("No news items found")
                return 0
            
            # Save news items to repository
            saved_count = 0
            for news in news_items:
                try:
                    news_id = await self.repository.save_news(news)
                    if news_id:
                        saved_count += 1
                        await self.event_bus.publish('news.parsed', {
                            'news_id': news_id,
                            'source': news.source,
                            'title': news.title[:100]
                        })
                except Exception as e:
                    self.logger.error(f"Error saving news '{news.title[:50]}...': {e}")
                    continue
            
            self.logger.info(f"Saved {saved_count} new news items from {len(news_items)} parsed")
            self.metrics.set_gauge("bot.save_success_rate", saved_count / len(news_items) if news_items else 0)
            
            return saved_count
            
        except Exception as e:
            self.logger.error(f"Error in parse and save: {e}")
            self.metrics.increment_counter("bot.parse_save_error")
            return 0
    
    @timed_metric(lambda self: self.metrics, "bot.process_content")
    async def _process_news_content(self) -> int:
        self.logger.info("Starting content processing")
        
        try:
            # Get unprocessed news
            unprocessed_news = await self.repository.get_news_by_status(
                NewsStatus.PARSED, 
                limit=self.config.parsing.max_news_per_run
            )
            
            if not unprocessed_news:
                self.logger.info("No news items to process")
                return 0
            
            processed_count = 0
            for news in unprocessed_news:
                try:
                    # Process content
                    rephrased_content = await self.content_processor.process_content(news)
                    
                    if rephrased_content:
                        # Update news status
                        success = await self.repository.update_news_status(
                            news.id,
                            NewsStatus.PROCESSED,
                            rephrased_content=rephrased_content
                        )
                        
                        if success:
                            processed_count += 1
                            await self.event_bus.publish('news.processed', {
                                'news_id': news.id,
                                'source': news.source,
                                'title': news.title[:100]
                            })
                        
                        # Delay between processing to respect rate limits
                        await asyncio.sleep(self.config.gigachat.delay_seconds)
                    else:
                        self.logger.warning(f"Failed to process content for news {news.id}")
                        await self.repository.update_news_status(news.id, NewsStatus.FAILED)
                
                except Exception as e:
                    self.logger.error(f"Error processing news {news.id}: {e}")
                    await self.repository.update_news_status(news.id, NewsStatus.FAILED)
                    continue
            
            self.logger.info(f"Processed {processed_count} news items")
            return processed_count
            
        except Exception as e:
            self.logger.error(f"Error in content processing: {e}")
            self.metrics.increment_counter("bot.process_content_error")
            return 0
    
    @timed_metric(lambda self: self.metrics, "bot.send_notifications")
    async def _send_notifications(self) -> int:
        self.logger.info("Starting notification sending")
        
        try:
            # Get processed news ready for sending
            ready_news = await self.repository.get_news_by_status(
                NewsStatus.PROCESSED,
                limit=5  # Limit to avoid spam
            )
            
            if not ready_news:
                self.logger.info("No news items ready for sending")
                return 0
            
            # Prepare news for sending
            news_to_send = []
            for news in ready_news:
                if news.rephrased_content:
                    news_to_send.append((news, news.rephrased_content))
            
            if not news_to_send:
                self.logger.warning("No news items have rephrased content")
                return 0
            
            # Send notifications
            results = await self.notification_service.send_multiple_news(news_to_send)
            
            # Update news status based on results
            sent_count = 0
            for news_id, message_id, success in results:
                if success:
                    await self.repository.update_news_status(
                        news_id,
                        NewsStatus.SENT,
                        telegram_message_id=message_id
                    )
                    sent_count += 1
                    
                    # Find the news item for event
                    news_item = next((n for n, _ in news_to_send if n.id == news_id), None)
                    if news_item:
                        await self.event_bus.publish('news.sent', {
                            'news_id': news_id,
                            'message_id': message_id,
                            'source': news_item.source,
                            'title': news_item.title[:100]
                        })
                else:
                    await self.repository.update_news_status(news_id, NewsStatus.FAILED)
            
            self.logger.info(f"Sent {sent_count} notifications")
            return sent_count
            
        except Exception as e:
            self.logger.error(f"Error in notification sending: {e}")
            self.metrics.increment_counter("bot.send_notifications_error")
            return 0
    
    async def get_statistics(self) -> Dict[str, Any]:
        try:
            db_stats = await self.repository.get_statistics()
            health_status = await self.health_checker.check_health()
            
            # Get metrics if available
            metrics_summary = {}
            if hasattr(self.metrics, 'get_all_metrics'):
                metrics_summary = self.metrics.get_all_metrics()
            
            return {
                'timestamp': datetime.now().isoformat(),
                'database_statistics': db_stats,
                'health_status': health_status,
                'metrics': metrics_summary
            }
            
        except Exception as e:
            self.logger.error(f"Error getting statistics: {e}")
            return {
                'timestamp': datetime.now().isoformat(),
                'error': str(e)
            }
    
    # Event handlers
    async def _on_news_parsed(self, event_type: str, data: Dict[str, Any]):
        self.logger.debug(f"News parsed: {data.get('title', 'Unknown')}")
    
    async def _on_news_processed(self, event_type: str, data: Dict[str, Any]):
        self.logger.debug(f"News processed: {data.get('title', 'Unknown')}")
    
    async def _on_news_sent(self, event_type: str, data: Dict[str, Any]):
        self.logger.debug(f"News sent: {data.get('title', 'Unknown')}")
    
    async def _on_error_occurred(self, event_type: str, data: Dict[str, Any]):
        self.logger.error(f"Error event: {data.get('error', 'Unknown error')}")
        self.metrics.increment_counter("bot.error_events")