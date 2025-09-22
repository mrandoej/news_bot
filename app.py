#!/usr/bin/env python3
"""
Refactored Saratov News Bot Application
Senior+ level architecture with DI, async/await, circuit breakers, metrics, and more
"""

import asyncio
import logging
import sys
import signal
from typing import Optional
from contextlib import asynccontextmanager

from core.container import DIContainer
from core.interfaces import (
    INewsParser, INewsRepository, IContentProcessor, INotificationService,
    IHealthChecker, IMetricsCollector, IEventBus
)
from core.metrics import InMemoryMetricsCollector
from core.event_bus import InMemoryEventBus
from core.validation import (
    NewsValidationChain, TitleValidator, ContentValidator, 
    UrlValidator, RegionKeywordValidator
)

from infrastructure.config_manager import ConfigManager
from infrastructure.logging_setup import LoggingSetup
from infrastructure.database_repository import AsyncNewsRepository

from services.parsing.news_parser_service import AsyncNewsParserService
from services.content_processor_service import GigaChatContentProcessor
from services.notification_service import TelegramNotificationService
from services.health_checker_service import HealthCheckerService
from services.news_bot_service import NewsBotService


class Application:
    def __init__(self):
        self.container: Optional[DIContainer] = None
        self.config = None
        self.logger = None
        self.bot_service: Optional[NewsBotService] = None
        self._shutdown_event = asyncio.Event()
    
    async def initialize(self):
        # Load configuration
        config_manager = ConfigManager()
        self.config = config_manager.load_config()
        
        # Setup logging
        LoggingSetup.setup_logging(self.config.logging)
        self.logger = logging.getLogger(__name__)
        
        self.logger.info("Initializing Saratov News Bot Application")
        
        # Setup DI container
        self.container = self._setup_container()
        
        # Initialize services
        await self._initialize_services()
        
        self.logger.info("Application initialized successfully")
    
    def _setup_container(self) -> DIContainer:
        container = DIContainer()
        
        # Register configuration parts
        container.register_instance(type(self.config), self.config)
        container.register_instance(type(self.config.database), self.config.database)
        container.register_instance(type(self.config.gigachat), self.config.gigachat)
        container.register_instance(type(self.config.telegram), self.config.telegram)
        container.register_instance(type(self.config.parsing), self.config.parsing)
        
        # Register core services
        container.register_singleton(IMetricsCollector, InMemoryMetricsCollector)
        container.register_singleton(IEventBus, InMemoryEventBus)
        
        # Register validation chain
        container.register_factory(NewsValidationChain, lambda: NewsValidationChain([
            TitleValidator(min_length=10, max_length=200),
            ContentValidator(min_length=20, max_length=5000),
            UrlValidator(),
            RegionKeywordValidator(
                self.config.region_keywords,
                self.config.exclude_keywords
            )
        ]))
        
        # Register infrastructure services with factory functions
        container.register_factory(INewsRepository, lambda: AsyncNewsRepository(
            self.config.database, 
            container.resolve(IMetricsCollector)
        ))
        
        # Register business services with factory functions
        container.register_factory(INewsParser, lambda: AsyncNewsParserService(
            self.config.parsing,
            self.config.news_sources,
            container.resolve(NewsValidationChain),
            container.resolve(IMetricsCollector)
        ))
        
        container.register_factory(IContentProcessor, lambda: GigaChatContentProcessor(
            self.config.gigachat,
            container.resolve(IMetricsCollector)
        ))
        
        container.register_factory(INotificationService, lambda: TelegramNotificationService(
            self.config.telegram,
            container.resolve(IMetricsCollector)
        ))
        
        container.register_factory(IHealthChecker, lambda: HealthCheckerService(
            container.resolve(INewsRepository),
            container.resolve(IContentProcessor),
            container.resolve(INotificationService),
            container.resolve(IMetricsCollector)
        ))
        
        container.register_factory(NewsBotService, lambda: NewsBotService(
            self.config,
            container.resolve(INewsParser),
            container.resolve(INewsRepository),
            container.resolve(IContentProcessor),
            container.resolve(INotificationService),
            container.resolve(IHealthChecker),
            container.resolve(IMetricsCollector),
            container.resolve(IEventBus)
        ))
        
        return container
    
    async def _initialize_services(self):
        # Initialize repository
        repository = self.container.resolve(INewsRepository)
        await repository.initialize()
        
        # Get main bot service
        self.bot_service = self.container.resolve(NewsBotService)
        
        self.logger.info("All services initialized")
    
    async def run_once(self):
        """Run single processing cycle"""
        if not self.bot_service:
            raise RuntimeError("Application not initialized")
        
        self.logger.info("Running single processing cycle")
        
        # Use context managers for services that need them
        async with self._get_service_contexts():
            result = await self.bot_service.run_full_cycle()
            
            if result.success:
                self.logger.info(f"Cycle completed successfully in {result.duration:.2f}s")
            else:
                self.logger.error(f"Cycle failed: {'; '.join(result.errors)}")
            
            return result
    
    async def run_scheduler(self):
        """Run with scheduler"""
        if not self.bot_service:
            raise RuntimeError("Application not initialized")
        
        self.logger.info(f"Starting scheduler with {self.config.parsing.interval_minutes} minute intervals")
        
        # Setup signal handlers
        self._setup_signal_handlers()
        
        async with self._get_service_contexts():
            # Run first cycle immediately
            await self.bot_service.run_full_cycle()
            
            # Schedule subsequent cycles
            while not self._shutdown_event.is_set():
                try:
                    # Wait for next cycle or shutdown
                    await asyncio.wait_for(
                        self._shutdown_event.wait(),
                        timeout=self.config.parsing.interval_minutes * 60
                    )
                    break  # Shutdown requested
                except asyncio.TimeoutError:
                    # Time for next cycle
                    await self.bot_service.run_full_cycle()
                except Exception as e:
                    self.logger.error(f"Error in scheduler loop: {e}")
                    await asyncio.sleep(60)  # Wait before retrying
        
        self.logger.info("Scheduler stopped")
    
    async def test_services(self):
        """Test all services"""
        if not self.bot_service:
            raise RuntimeError("Application not initialized")
        
        self.logger.info("Testing all services")
        
        async with self._get_service_contexts():
            health_checker = self.container.resolve(IHealthChecker)
            health_status = await health_checker.check_health()
            
            print("\n" + "=" * 50)
            print("SERVICE HEALTH CHECK")
            print("=" * 50)
            
            for service, status in health_status.items():
                status_icon = "✅" if status else "❌"
                print(f"{status_icon} {service.replace('_', ' ').title()}: {'OK' if status else 'FAILED'}")
            
            print("=" * 50)
            
            return health_status.get('overall', False)
    
    async def show_statistics(self):
        """Show application statistics"""
        if not self.bot_service:
            raise RuntimeError("Application not initialized")
        
        stats = await self.bot_service.get_statistics()
        
        print("\n" + "=" * 50)
        print("APPLICATION STATISTICS")
        print("=" * 50)
        print(f"Timestamp: {stats['timestamp']}")
        
        # Database statistics
        db_stats = stats.get('database_statistics', {})
        print(f"\nDatabase:")
        print(f"  Total news: {db_stats.get('total_news', 0)}")
        
        by_status = db_stats.get('by_status', {})
        for status, count in by_status.items():
            print(f"  {status.title()}: {count}")
        
        print(f"\nBy source:")
        by_source = db_stats.get('by_source', {})
        for source, count in sorted(by_source.items(), key=lambda x: x[1], reverse=True):
            print(f"  {source}: {count}")
        
        print(f"\nBy city:")
        by_city = db_stats.get('by_city', {})
        for city, count in sorted(by_city.items(), key=lambda x: x[1], reverse=True):
            print(f"  {city}: {count}")
        
        # Health status
        health_status = stats.get('health_status', {})
        print(f"\nHealth Status:")
        for service, status in health_status.items():
            status_icon = "✅" if status else "❌"
            print(f"  {status_icon} {service.replace('_', ' ').title()}")
        
        print("=" * 50)
    
    @asynccontextmanager
    async def _get_service_contexts(self):
        """Context manager for services that need async context management"""
        parser = self.container.resolve(INewsParser)
        content_processor = self.container.resolve(IContentProcessor)
        
        async with parser, content_processor:
            yield
    
    def _setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown"""
        def signal_handler(signum, frame):
            self.logger.info(f"Received signal {signum}, initiating shutdown")
            self._shutdown_event.set()
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    
    async def shutdown(self):
        """Graceful shutdown"""
        self.logger.info("Shutting down application")
        self._shutdown_event.set()


async def main():
    """Main application entry point"""
    app = Application()
    
    try:
        await app.initialize()
        
        # Parse command line arguments
        command = sys.argv[1].lower() if len(sys.argv) > 1 else "run"
        
        if command == "once":
            # Single run
            result = await app.run_once()
            sys.exit(0 if result.success else 1)
            
        elif command == "test":
            # Test services
            success = await app.test_services()
            sys.exit(0 if success else 1)
            
        elif command == "stats":
            # Show statistics
            await app.show_statistics()
            
        elif command == "run":
            # Run scheduler
            await app.run_scheduler()
            
        else:
            print("Available commands:")
            print("  python app.py run     - run with scheduler (default)")
            print("  python app.py once    - single run")
            print("  python app.py test    - test all services")
            print("  python app.py stats   - show statistics")
            sys.exit(1)
    
    except KeyboardInterrupt:
        logging.info("Application interrupted by user")
    except Exception as e:
        logging.error(f"Critical application error: {e}")
        sys.exit(1)
    finally:
        await app.shutdown()


if __name__ == "__main__":
    # Run the application
    asyncio.run(main())