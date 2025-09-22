import aiohttp
import asyncio
import logging
from typing import List, Dict

from core.interfaces import INewsParser, NewsItem, SourceConfig
from core.validation import NewsValidationChain, INewsValidator
from core.circuit_breaker import CircuitBreaker
from core.metrics import timed_metric, IMetricsCollector
from infrastructure.config_manager import ParsingConfig
from .strategies import ParsingStrategyFactory


class AsyncNewsParserService(INewsParser):
    def __init__(
        self, 
        config: ParsingConfig,
        sources: Dict[str, SourceConfig],
        validation_chain: NewsValidationChain,
        metrics: IMetricsCollector
    ):
        self.config = config
        self.sources = sources
        self.validation_chain = validation_chain
        self.metrics = metrics
        self.logger = logging.getLogger(__name__)
        
        # Circuit breakers for each source
        self.circuit_breakers = {
            name: CircuitBreaker(failure_threshold=3, recovery_timeout=300.0)
            for name in sources.keys()
        }
        
        # HTTP session configuration
        self.connector = aiohttp.TCPConnector(
            limit=100,
            limit_per_host=10,
            ttl_dns_cache=300,
            use_dns_cache=True,
        )
        
        self.session_timeout = aiohttp.ClientTimeout(
            total=config.timeout,
            connect=10,
            sock_read=30
        )
    
    async def __aenter__(self):
        self.session = aiohttp.ClientSession(
            connector=self.connector,
            timeout=self.session_timeout,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
        )
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if hasattr(self, 'session'):
            await self.session.close()
    
    @timed_metric(lambda self: self.metrics, "parser.parse_all_sources")
    async def parse_all_sources(self) -> List[NewsItem]:
        enabled_sources = {
            name: source for name, source in self.sources.items() 
            if source.enabled
        }
        
        if not enabled_sources:
            self.logger.warning("No enabled sources found")
            return []
        
        self.logger.info(f"Starting to parse {len(enabled_sources)} sources")
        
        # Create semaphore to limit concurrent parsing
        semaphore = asyncio.Semaphore(self.config.max_concurrent_sources)
        
        # Create tasks for all sources
        tasks = [
            self._parse_source_with_semaphore(semaphore, name, source)
            for name, source in enabled_sources.items()
        ]
        
        # Execute all tasks concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Collect all news items
        all_news = []
        failed_sources = []
        
        for i, result in enumerate(results):
            source_name = list(enabled_sources.keys())[i]
            
            if isinstance(result, Exception):
                self.logger.error(f"Source {source_name} failed: {result}")
                failed_sources.append(source_name)
                self.metrics.increment_counter("parser.source_failed", {"source": source_name})
            else:
                all_news.extend(result)
                self.metrics.increment_counter("parser.source_success", {"source": source_name})
                self.metrics.set_gauge("parser.news_count", len(result), {"source": source_name})
        
        if failed_sources:
            self.logger.warning(f"Failed sources: {', '.join(failed_sources)}")
        
        # Validate all news items
        validated_news = []
        for news in all_news:
            try:
                self.validation_chain.validate(news)
                validated_news.append(news)
                self.metrics.increment_counter("parser.news_validated", {"source": news.source})
            except Exception as e:
                self.logger.debug(f"News validation failed: {e}")
                self.metrics.increment_counter("parser.news_rejected", {"source": news.source})
        
        self.logger.info(
            f"Parsing completed: {len(validated_news)} valid news from "
            f"{len(enabled_sources) - len(failed_sources)} sources"
        )
        
        self.metrics.set_gauge("parser.total_news", len(validated_news))
        return validated_news
    
    async def _parse_source_with_semaphore(self, semaphore: asyncio.Semaphore, name: str, source: SourceConfig) -> List[NewsItem]:
        async with semaphore:
            return await self.parse_source(source)
    
    @timed_metric(lambda self: self.metrics, "parser.parse_source")
    async def parse_source(self, source: SourceConfig) -> List[NewsItem]:
        circuit_breaker = self.circuit_breakers.get(source.name)
        if not circuit_breaker:
            circuit_breaker = CircuitBreaker()
            self.circuit_breakers[source.name] = circuit_breaker
        
        try:
            # Check if source is available first
            if not await self.is_source_available(source):
                self.logger.warning(f"Source {source.name} is not available")
                return []
            
            # Use circuit breaker to call parsing strategy
            return await circuit_breaker.call(self._parse_source_internal, source)
            
        except Exception as e:
            self.logger.error(f"Error parsing source {source.name}: {e}")
            self.metrics.increment_counter("parser.source_error", {"source": source.name})
            return []
    
    async def _parse_source_internal(self, source: SourceConfig) -> List[NewsItem]:
        self.logger.debug(f"Parsing source: {source.name}")
        
        # Create parsing strategy
        strategy = ParsingStrategyFactory.create_strategy(
            source, self.metrics, self.config.max_news_per_run
        )
        
        # Parse using strategy
        news_items = await strategy.parse(source, self.session)
        
        self.logger.info(f"Source {source.name}: parsed {len(news_items)} items")
        return news_items
    
    @timed_metric(lambda self: self.metrics, "parser.check_availability")
    async def is_source_available(self, source: SourceConfig) -> bool:
        try:
            async with self.session.head(
                source.url, 
                timeout=aiohttp.ClientTimeout(total=10),
                allow_redirects=True
            ) as response:
                is_available = response.status < 400
                
                if is_available:
                    self.metrics.increment_counter("parser.availability_check_success", {"source": source.name})
                else:
                    self.metrics.increment_counter("parser.availability_check_failed", {"source": source.name})
                
                return is_available
                
        except Exception as e:
            self.logger.debug(f"Source {source.name} availability check failed: {e}")
            self.metrics.increment_counter("parser.availability_check_error", {"source": source.name})
            return False