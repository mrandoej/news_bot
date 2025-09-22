import os
import json
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from dotenv import load_dotenv
from core.interfaces import SourceConfig

load_dotenv()


@dataclass(frozen=True)
class DatabaseConfig:
    path: str = './data/news.db'
    connection_pool_size: int = 5
    timeout: float = 30.0


@dataclass(frozen=True)
class GigaChatConfig:
    credentials: str
    scope: str = 'GIGACHAT_API_PERS'
    base_url: str = 'https://gigachat.devices.sberbank.ru/api/v1'
    auth_url: str = 'https://ngw.devices.sberbank.ru:9443/api/v2/oauth'
    timeout: float = 60.0
    delay_seconds: float = 2.0
    max_retries: int = 3
    verify_ssl: bool = False


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    channel_id: str
    message_limit: int = 4000
    delay_seconds: float = 2.0
    timeout: float = 30.0


@dataclass(frozen=True)
class ParsingConfig:
    interval_minutes: int = 30
    max_news_per_run: int = 10
    min_content_length: int = 100
    max_content_length: int = 2000
    timeout: float = 30.0
    max_concurrent_sources: int = 5


@dataclass(frozen=True)
class LoggingConfig:
    level: str = 'INFO'
    file: str = './logs/bot.log'
    format: str = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    max_file_size: int = 10 * 1024 * 1024  # 10MB
    backup_count: int = 5


@dataclass(frozen=True)
class CircuitBreakerConfig:
    failure_threshold: int = 5
    recovery_timeout: float = 60.0


@dataclass(frozen=True)
class RetryConfig:
    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    exponential_base: float = 2.0
    jitter: bool = True


@dataclass(frozen=True)
class AppConfig:
    database: DatabaseConfig
    gigachat: GigaChatConfig
    telegram: TelegramConfig
    parsing: ParsingConfig
    logging: LoggingConfig
    circuit_breaker: CircuitBreakerConfig
    retry: RetryConfig
    news_sources: Dict[str, SourceConfig] = field(default_factory=dict)
    region_keywords: List[str] = field(default_factory=list)
    exclude_keywords: List[str] = field(default_factory=list)
    cleanup_hour: int = 3


class ConfigManager:
    def __init__(self):
        self._config: Optional[AppConfig] = None
    
    def load_config(self) -> AppConfig:
        if self._config is None:
            self._config = self._build_config()
        return self._config
    
    def _build_config(self) -> AppConfig:
        # Validate required environment variables
        required_vars = {
            'GIGACHAT_CREDENTIALS': os.getenv('GIGACHAT_CREDENTIALS'),
            'TELEGRAM_BOT_TOKEN': os.getenv('TELEGRAM_BOT_TOKEN'),
            'TELEGRAM_CHANNEL_ID': os.getenv('TELEGRAM_CHANNEL_ID')
        }
        
        missing_vars = [var for var, value in required_vars.items() if not value]
        if missing_vars:
            raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")
        
        return AppConfig(
            database=DatabaseConfig(
                path=os.getenv('DATABASE_PATH', './data/news.db'),
                connection_pool_size=int(os.getenv('DB_POOL_SIZE', '5')),
                timeout=float(os.getenv('DB_TIMEOUT', '30.0'))
            ),
            gigachat=GigaChatConfig(
                credentials=required_vars['GIGACHAT_CREDENTIALS'],
                scope=os.getenv('GIGACHAT_SCOPE', 'GIGACHAT_API_PERS'),
                base_url=os.getenv('GIGACHAT_BASE_URL', 'https://gigachat.devices.sberbank.ru/api/v1'),
                auth_url=os.getenv('GIGACHAT_AUTH_URL', 'https://ngw.devices.sberbank.ru:9443/api/v2/oauth'),
                timeout=float(os.getenv('GIGACHAT_TIMEOUT', '60.0')),
                delay_seconds=float(os.getenv('GIGACHAT_DELAY_SECONDS', '2.0')),
                max_retries=int(os.getenv('GIGACHAT_MAX_RETRIES', '3')),
                verify_ssl=os.getenv('GIGACHAT_VERIFY_SSL', 'false').lower() == 'true'
            ),
            telegram=TelegramConfig(
                bot_token=required_vars['TELEGRAM_BOT_TOKEN'],
                channel_id=required_vars['TELEGRAM_CHANNEL_ID'],
                message_limit=int(os.getenv('TELEGRAM_MESSAGE_LIMIT', '4000')),
                delay_seconds=float(os.getenv('TELEGRAM_DELAY_SECONDS', '2.0')),
                timeout=float(os.getenv('TELEGRAM_TIMEOUT', '30.0'))
            ),
            parsing=ParsingConfig(
                interval_minutes=int(os.getenv('PARSE_INTERVAL_MINUTES', '30')),
                max_news_per_run=int(os.getenv('MAX_NEWS_PER_RUN', '10')),
                min_content_length=int(os.getenv('MIN_CONTENT_LENGTH', '100')),
                max_content_length=int(os.getenv('MAX_CONTENT_LENGTH', '2000')),
                timeout=float(os.getenv('PARSING_TIMEOUT', '30.0')),
                max_concurrent_sources=int(os.getenv('MAX_CONCURRENT_SOURCES', '5'))
            ),
            logging=LoggingConfig(
                level=os.getenv('LOG_LEVEL', 'INFO'),
                file=os.getenv('LOG_FILE', './logs/bot.log'),
                format=os.getenv('LOG_FORMAT', '%(asctime)s - %(name)s - %(levelname)s - %(message)s'),
                max_file_size=int(os.getenv('LOG_MAX_FILE_SIZE', str(10 * 1024 * 1024))),
                backup_count=int(os.getenv('LOG_BACKUP_COUNT', '5'))
            ),
            circuit_breaker=CircuitBreakerConfig(
                failure_threshold=int(os.getenv('CIRCUIT_BREAKER_FAILURE_THRESHOLD', '5')),
                recovery_timeout=float(os.getenv('CIRCUIT_BREAKER_RECOVERY_TIMEOUT', '60.0'))
            ),
            retry=RetryConfig(
                max_attempts=int(os.getenv('RETRY_MAX_ATTEMPTS', '3')),
                base_delay=float(os.getenv('RETRY_BASE_DELAY', '1.0')),
                max_delay=float(os.getenv('RETRY_MAX_DELAY', '60.0')),
                exponential_base=float(os.getenv('RETRY_EXPONENTIAL_BASE', '2.0')),
                jitter=os.getenv('RETRY_JITTER', 'true').lower() == 'true'
            ),
            news_sources=self._load_news_sources(),
            region_keywords=self._load_region_keywords(),
            exclude_keywords=self._load_exclude_keywords(),
            cleanup_hour=int(os.getenv('CLEANUP_HOUR', '3'))
        )
    
    def _load_news_sources(self) -> Dict[str, SourceConfig]:
        """
        Загружает источники новостей из файла конфигурации или переменных окружения.
        Приоритет: переменные окружения > sources.json > встроенные источники
        """
        sources = {}
        
        # 1. Попробуем загрузить из JSON файла
        sources_file = os.getenv('NEWS_SOURCES_FILE', './sources.json')
        if os.path.exists(sources_file):
            try:
                with open(sources_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for source_data in data.get('sources', []):
                        source_id = source_data['id']
                        sources[source_id] = SourceConfig(
                            name=source_data['name'],
                            url=source_data['url'],
                            city=source_data.get('city', 'Саратов'),
                            rss=source_data.get('rss'),
                            selector=source_data.get('selector'),
                            enabled=source_data.get('enabled', True),
                            priority=source_data.get('priority', 1),
                            timeout=source_data.get('timeout', 30)
                        )
            except Exception as e:
                print(f"Warning: Failed to load sources from {sources_file}: {e}")
        
        # 2. Переопределяем настройки из переменных окружения
        disabled_sources = os.getenv('DISABLED_SOURCES', '').split(',')
        disabled_sources = [s.strip() for s in disabled_sources if s.strip()]
        
        enabled_sources = os.getenv('ENABLED_SOURCES', '').split(',')
        enabled_sources = [s.strip() for s in enabled_sources if s.strip()]
        
        # Если указаны только включенные источники, отключаем все остальные
        if enabled_sources:
            for source_id in sources:
                sources[source_id] = SourceConfig(
                    name=sources[source_id].name,
                    url=sources[source_id].url,
                    city=sources[source_id].city,
                    rss=sources[source_id].rss,
                    selector=sources[source_id].selector,
                    enabled=source_id in enabled_sources,
                    priority=sources[source_id].priority,
                    timeout=sources[source_id].timeout
                )
        
        # Отключаем источники из DISABLED_SOURCES
        for source_id in disabled_sources:
            if source_id in sources:
                sources[source_id] = SourceConfig(
                    name=sources[source_id].name,
                    url=sources[source_id].url,
                    city=sources[source_id].city,
                    rss=sources[source_id].rss,
                    selector=sources[source_id].selector,
                    enabled=False,
                    priority=sources[source_id].priority,
                    timeout=sources[source_id].timeout
                )
        
        # 3. Если ничего не загрузилось, используем встроенные источники
        if not sources:
            sources = self._get_default_sources()
        
        return sources
    
    def _get_default_sources(self) -> Dict[str, SourceConfig]:
        """Встроенные источники по умолчанию"""
        return {
            'lenta_ru': SourceConfig(
                name='Лента.ру (Саратов)',
                url='https://lenta.ru',
                city='Саратов',
                rss='https://lenta.ru/rss/news'
            ),
            'ria_novosti': SourceConfig(
                name='РИА Новости (Регионы)',
                url='https://ria.ru',
                city='Саратов',
                rss='https://ria.ru/export/rss2/archive/index.xml'
            ),
            'interfax': SourceConfig(
                name='Интерфакс (Россия)',
                url='https://www.interfax.ru',
                city='Саратов',
                rss='https://www.interfax.ru/rss.asp'
            ),
            'tass': SourceConfig(
                name='ТАСС (Регионы)',
                url='https://tass.ru',
                city='Саратов',
                rss='https://tass.ru/rss/v2.xml'
            )
        }
    
    def _load_region_keywords(self) -> List[str]:
        return [
            # Основные города Саратовской области
            'саратов', 'энгельс', 'балаково', 'маркс', 'хвалынск', 
            'вольск', 'ртищево', 'красноармейск', 'пугачев', 'аткарск',
            'балашов', 'петровск', 'новоузенск', 'калининск', 'красный кут',
            'ершов', 'озинки', 'дергачи', 'духовницкое', 'лысые горы',
            
            # Специфичные для области термины
            'саратовская область', 'саратовский', 'саратовской области',
            'правительство саратовской области', 'губернатор саратовской области',
            'администрация саратова', 'мэр саратова', 'дума саратова',
            'саратовская дума', 'саратовская администрация',
            
            # Районы Саратовской области
            'аркадакский район', 'аткарский район', 'базарно-карабулакский район',
            'балаковский район', 'балашовский район', 'балтайский район',
            'вольский район', 'воскресенский район', 'дергачевский район',
            'духовницкий район', 'екатериновский район', 'ершовский район',
            'ивантеевский район', 'калининский район', 'красноармейский район',
            'краснокутский район', 'краснопартизанский район', 'лысогорский район',
            'марксовский район', 'новобурасский район', 'новоузенский район',
            'озинский район', 'перелюбский район', 'петровский район',
            'питерский район', 'пугачевский район', 'ровенский район',
            'романовский район', 'ртищевский район', 'самойловский район',
            'советский район', 'татищевский район', 'турковский район',
            'федоровский район', 'хвалынский район', 'энгельсский район'
        ]
    
    def _load_exclude_keywords(self) -> List[str]:
        return [
            'реклама', 'объявление', 'продам', 'куплю', 'сдам', 'сниму',
            'знакомства', 'интим', 'эскорт', 'казино', 'ставки'
        ]