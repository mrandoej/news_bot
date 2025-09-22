from abc import ABC, abstractmethod
from typing import List, Dict, Optional, Any, AsyncIterator, Tuple
from datetime import datetime
from dataclasses import dataclass, field, replace
from enum import Enum
import asyncio


class NewsStatus(Enum):
    PARSED = "parsed"
    PROCESSED = "processed"
    SENT = "sent"
    FAILED = "failed"


@dataclass(frozen=True)
class NewsItem:
    title: str
    content: str
    url: str
    source: str
    city: Optional[str] = None
    published_date: Optional[datetime] = None
    status: NewsStatus = NewsStatus.PARSED
    id: Optional[int] = None
    rephrased_content: Optional[str] = None
    telegram_message_id: Optional[int] = None
    created_date: Optional[datetime] = None
    
    def with_status(self, status: NewsStatus, **kwargs) -> 'NewsItem':
        return replace(self, status=status, **kwargs)


@dataclass(frozen=True)
class SourceConfig:
    name: str
    url: str
    city: str
    rss: Optional[str] = None
    selector: Optional[str] = None
    enabled: bool = True
    priority: int = 1
    timeout: int = 30


@dataclass(frozen=True)
class ProcessingResult:
    success: bool
    processed_count: int
    failed_count: int
    errors: List[str] = field(default_factory=list)
    duration: float = 0.0


class INewsParser(ABC):
    @abstractmethod
    async def parse_source(self, source: SourceConfig) -> List[NewsItem]:
        pass
    
    @abstractmethod
    async def parse_all_sources(self) -> List[NewsItem]:
        pass
    
    @abstractmethod
    async def is_source_available(self, source: SourceConfig) -> bool:
        pass


class INewsRepository(ABC):
    @abstractmethod
    async def save_news(self, news: NewsItem) -> Optional[int]:
        pass
    
    @abstractmethod
    async def get_news_by_status(self, status: NewsStatus, limit: int = 10) -> List[NewsItem]:
        pass
    
    @abstractmethod
    async def update_news_status(self, news_id: int, status: NewsStatus, **kwargs) -> bool:
        pass
    
    @abstractmethod
    async def news_exists(self, news: NewsItem) -> bool:
        pass
    
    @abstractmethod
    async def get_statistics(self) -> Dict[str, Any]:
        pass
    
    @abstractmethod
    async def cleanup_old_news(self, days: int) -> int:
        pass


class IContentProcessor(ABC):
    @abstractmethod
    async def process_content(self, news: NewsItem) -> Optional[str]:
        pass
    
    @abstractmethod
    async def is_available(self) -> bool:
        pass


class INotificationService(ABC):
    @abstractmethod
    async def send_news(self, news: NewsItem, content: str) -> Optional[int]:
        pass
    
    @abstractmethod
    async def send_multiple_news(self, news_items: List[Tuple[NewsItem, str]]) -> List[Tuple[int, Optional[int], bool]]:
        pass
    
    @abstractmethod
    async def is_available(self) -> bool:
        pass


class IHealthChecker(ABC):
    @abstractmethod
    async def check_health(self) -> Dict[str, bool]:
        pass


class IMetricsCollector(ABC):
    @abstractmethod
    def increment_counter(self, metric: str, tags: Dict[str, str] = None):
        pass
    
    @abstractmethod
    def record_duration(self, metric: str, duration: float, tags: Dict[str, str] = None):
        pass
    
    @abstractmethod
    def set_gauge(self, metric: str, value: float, tags: Dict[str, str] = None):
        pass


class IEventBus(ABC):
    @abstractmethod
    async def publish(self, event_type: str, data: Dict[str, Any]):
        pass
    
    @abstractmethod
    def subscribe(self, event_type: str, handler):
        pass


class ICircuitBreaker(ABC):
    @abstractmethod
    async def call(self, func, *args, **kwargs):
        pass
    
    @abstractmethod
    def is_open(self) -> bool:
        pass


class NewsValidationError(Exception):
    pass


class ServiceUnavailableError(Exception):
    pass


class ProcessingError(Exception):
    pass


class CircuitBreakerOpenError(Exception):
    pass