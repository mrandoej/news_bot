import aiohttp
import asyncio
import uuid
import time
import logging
import re
from typing import Optional, Dict, Any
from dataclasses import dataclass

from core.interfaces import IContentProcessor, NewsItem
from core.circuit_breaker import CircuitBreaker
from core.retry import smart_retry
from core.metrics import timed_metric, IMetricsCollector
from infrastructure.config_manager import GigaChatConfig


@dataclass
class TokenInfo:
    access_token: str
    expires_at: int
    created_at: float


class GigaChatContentProcessor(IContentProcessor):
    def __init__(self, config: GigaChatConfig, metrics: IMetricsCollector):
        self.config = config
        self.metrics = metrics
        self.logger = logging.getLogger(__name__)
        self.token_info: Optional[TokenInfo] = None
        self.circuit_breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=300.0)
        
        # Sensitive topics that GigaChat might block
        self.sensitive_keywords = [
            'путин', 'зеленский', 'байден', 'трамп', 'навальный', 'оппозиция',
            'выборы', 'голосование', 'референдум', 'протест', 'митинг', 'демонстрация',
            'санкции', 'блокировка', 'запрет', 'цензура', 'репрессии',
            'война', 'военный', 'армия', 'солдат', 'офицер', 'генерал',
            'украина', 'донбасс', 'луганск', 'донецк', 'крым', 'херсон',
            'сво', 'спецоперация', 'мобилизация', 'призыв', 'военкомат',
            'оружие', 'танк', 'самолет', 'ракета', 'бомба', 'взрыв',
            'атака', 'обстрел', 'удар', 'наступление', 'оборона',
            'коррупция', 'взятка', 'откат', 'хищение', 'мошенничество',
            'убийство', 'смерть', 'теракт', 'взрыв', 'пожар', 'авария',
            'катастрофа', 'трагедия', 'жертвы', 'пострадавшие'
        ]
        
        self.gigachat_block_phrases = [
            'чувствительными темами',
            'временно ограничены',
            'некорректные ответы',
            'открытых источников',
            'неправильного толкования',
            'генеративные языковые модели',
            'благодарим за понимание'
        ]
    
    async def __aenter__(self):
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.config.timeout),
            headers={'User-Agent': 'SaratovNewsBot/1.0'}
        )
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if hasattr(self, 'session'):
            await self.session.close()
    
    @timed_metric(lambda self: self.metrics, "content_processor.process_content")
    async def process_content(self, news: NewsItem) -> Optional[str]:
        try:
            # Check if content contains sensitive topics
            if self._is_sensitive_topic(news.title, news.content):
                self.logger.info(f"Sensitive topic detected, using alternative processing: {news.title[:50]}...")
                self.metrics.increment_counter("content_processor.sensitive_topic")
                return self._create_alternative_rephrasing(news.title, news.content, news.city)
            
            # Try GigaChat processing
            try:
                result = await self.circuit_breaker.call(self._process_with_gigachat, news)
                
                # Check if GigaChat blocked the response
                if result and self._is_gigachat_blocked_response(result):
                    self.logger.warning(f"GigaChat blocked response, using alternative: {news.title[:50]}...")
                    self.metrics.increment_counter("content_processor.gigachat_blocked")
                    return self._create_alternative_rephrasing(news.title, news.content, news.city)
                
                if result:
                    self.metrics.increment_counter("content_processor.gigachat_success")
                    return result
                    
            except Exception as e:
                self.logger.warning(f"GigaChat processing failed, using alternative: {e}")
                self.metrics.increment_counter("content_processor.gigachat_error")
            
            # Fallback to alternative processing
            self.metrics.increment_counter("content_processor.alternative_used")
            return self._create_alternative_rephrasing(news.title, news.content, news.city)
            
        except Exception as e:
            self.logger.error(f"Critical error in content processing: {e}")
            self.metrics.increment_counter("content_processor.critical_error")
            return f"Заголовок: {news.title}\nТекст: {news.content}"
    
    @smart_retry(max_attempts=2, base_delay=2.0, exceptions=(aiohttp.ClientError, asyncio.TimeoutError))
    async def _process_with_gigachat(self, news: NewsItem) -> Optional[str]:
        token = await self._get_access_token()
        if not token:
            raise Exception("Failed to get access token")
        
        city_info = f" из города {news.city}" if news.city else ""
        
        system_prompt = """Ты — главный редактор Telegram-канала "Саратов Онлайн". Тебе нужно писать короткие, живые и полезные новости для местных жителей.

## Задачи:

### Пересказ новостей:
- Преобразуй полученную новость в короткий текст (2–4 предложения) своими словами.
- Текст должен быть грамотным, понятным и без официальной терминологии.
- Избегай использования жаргона и мемов.
- Включай один соответствующий эмодзи в начало поста.
- Изменяй тон сообщения в зависимости от характера новости (нейтральный, серьёзный, радостный, слегка ироничный).

### Определение значимости:
Перед тем, как опубликовать новость, ответь себе на вопрос: "Это важно для большинства подписчиков?"
Если ответ отрицательный, сократи сообщение до минимального размера или пропусти эту новость вовсе.

### Практическая польза:
Если новость имеет практическую пользу, добавь короткую рекомендацию в конце (где это произойдёт, что делать, куда обратиться и т.д.).

### Правила публикации:
- Не выкладывай пресс-релизы без фактической информации.
- Игнорируй анонсы без конкретных деталей ("Скоро будет..." и т.п.).
- Откажись от публикаций, если они не несут ценности для пользователей.

## Форматы:

### Обычные посты:
```
[Эмодзи] [Краткий пересказ новости]
[Дополнительная фраза, если нужна]
#[Тема] #[Место] #НовостиСаратова
```

## Запрещено:
- Придумывать факты.
- Использовать неуместные эмодзи в тревожных новостях.
- Публиковать непроверенные данные.
- Если возникают сомнения в достоверности новости, указывай: "Информация уточняется".

Помни: главное — помогать людям понимать происходящее вокруг них просто и понятно."""
        
        user_prompt = f"""Перефрази следующую новость{city_info}:

Заголовок: {news.title}

Текст: {news.content}

Верни результат в формате:
Заголовок: [перефразированный заголовок]
Текст: [перефразированный текст]"""
        
        payload = {
            "model": "GigaChat",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.7,
            "max_tokens": 2000,
            "stream": False
        }
        
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {token}'
        }
        
        async with self.session.post(
            f"{self.config.base_url}/chat/completions",
            headers=headers,
            json=payload,
            ssl=self.config.verify_ssl
        ) as response:
            if response.status == 200:
                result = await response.json()
                
                if 'choices' not in result or not result['choices']:
                    raise Exception("Empty response from API")
                
                rephrased_text = result['choices'][0]['message']['content']
                
                if not rephrased_text or not rephrased_text.strip():
                    raise Exception("Empty rephrased text received")
                
                self.logger.info(f"News successfully rephrased via GigaChat: {news.title[:50]}...")
                return rephrased_text.strip()
                
            elif response.status == 401:
                self.token_info = None
                raise Exception("Authentication error")
            elif response.status == 429:
                raise Exception("Rate limit exceeded")
            else:
                raise Exception(f"API error: {response.status}")
    
    @smart_retry(max_attempts=3, base_delay=1.0, exceptions=(aiohttp.ClientError, asyncio.TimeoutError))
    async def _get_access_token(self) -> Optional[str]:
        if self._is_token_valid():
            return self.token_info.access_token
        
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'RqUID': str(uuid.uuid4()),
            'Authorization': f'Basic {self.config.credentials}'
        }
        
        data = {'scope': self.config.scope}
        
        async with self.session.post(
            self.config.auth_url,
            headers=headers,
            data=data,
            ssl=self.config.verify_ssl
        ) as response:
            if response.status == 200:
                token_data = await response.json()
                
                self.token_info = TokenInfo(
                    access_token=token_data['access_token'],
                    expires_at=token_data['expires_at'] / 1000 if token_data['expires_at'] > 9999999999 else token_data['expires_at'],
                    created_at=time.time()
                )
                
                expires_in_minutes = (self.token_info.expires_at - time.time()) / 60
                self.logger.info(f"GigaChat access token obtained, expires in {expires_in_minutes:.1f} minutes")
                return self.token_info.access_token
                
            else:
                raise Exception(f"Token request failed: {response.status}")
    
    def _is_token_valid(self) -> bool:
        if not self.token_info:
            return False
        
        current_time = time.time()
        buffer_time = 60  # 1 minute buffer
        
        return current_time < (self.token_info.expires_at - buffer_time)
    
    @smart_retry(max_attempts=3, base_delay=1.0, exceptions=(aiohttp.ClientError, asyncio.TimeoutError))
    async def _get_access_token_with_session(self, session: aiohttp.ClientSession) -> Optional[str]:
        if self._is_token_valid():
            return self.token_info.access_token
        
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'RqUID': str(uuid.uuid4()),
            'Authorization': f'Basic {self.config.credentials}'
        }
        
        data = {'scope': self.config.scope}
        
        async with session.post(
            self.config.auth_url,
            headers=headers,
            data=data,
            ssl=self.config.verify_ssl
        ) as response:
            if response.status == 200:
                token_data = await response.json()
                
                self.token_info = TokenInfo(
                    access_token=token_data['access_token'],
                    expires_at=token_data['expires_at'] / 1000 if token_data['expires_at'] > 9999999999 else token_data['expires_at'],
                    created_at=time.time()
                )
                
                expires_in_minutes = (self.token_info.expires_at - time.time()) / 60
                self.logger.info(f"GigaChat access token obtained, expires in {expires_in_minutes:.1f} minutes")
                return self.token_info.access_token
                
            else:
                raise Exception(f"Token request failed: {response.status}")
    
    @timed_metric(lambda self: self.metrics, "content_processor.check_availability")
    async def is_available(self) -> bool:
        try:
            # Create temporary session for availability check
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10),
                headers={'User-Agent': 'SaratovNewsBot/1.0'}
            ) as session:
                token = await self._get_access_token_with_session(session)
                if not token:
                    return False
                
                headers = {'Authorization': f'Bearer {token}'}
                
                async with session.get(
                    f"{self.config.base_url}/models",
                    headers=headers,
                    ssl=self.config.verify_ssl
                ) as response:
                    is_available = response.status == 200
                    
                    if is_available:
                        self.metrics.increment_counter("content_processor.availability_success")
                    else:
                        self.metrics.increment_counter("content_processor.availability_failed")
                    
                    return is_available
                
        except Exception as e:
            self.logger.error(f"GigaChat availability check failed: {e}")
            self.metrics.increment_counter("content_processor.availability_error")
            return False
    
    def _is_sensitive_topic(self, title: str, content: str) -> bool:
        text = f"{title} {content}".lower()
        
        for keyword in self.sensitive_keywords:
            if keyword in text:
                self.logger.debug(f"Sensitive keyword detected: {keyword}")
                return True
        
        return False
    
    def _is_gigachat_blocked_response(self, response_text: str) -> bool:
        if not response_text:
            return False
        
        text = response_text.lower()
        
        for phrase in self.gigachat_block_phrases:
            if phrase in text:
                return True
        
        return False
    
    def _create_alternative_rephrasing(self, title: str, content: str, city: str = None) -> str:
        try:
            rephrased_title = self._simple_rephrase_title(title)
            rephrased_content = self._simple_rephrase_content(content)
            
            city_info = f" ({city})" if city else ""
            
            result = f"Заголовок: {rephrased_title}{city_info}\n"
            result += f"Текст: {rephrased_content}"
            
            self.logger.info(f"Alternative rephrasing created for: {title[:50]}...")
            return result
            
        except Exception as e:
            self.logger.error(f"Error in alternative rephrasing: {e}")
            return f"Заголовок: {title}\nТекст: {content}"
    
    def _simple_rephrase_title(self, title: str) -> str:
        replacements = {
            'сообщает': 'информирует',
            'заявил': 'отметил',
            'рассказал': 'поделился информацией',
            'объявил': 'сообщил',
            'планирует': 'намерен',
            'будет': 'планируется',
            'прошел': 'состоялся',
            'началось': 'стартовало',
            'завершилось': 'подошло к концу'
        }
        
        result = title
        for old, new in replacements.items():
            result = re.sub(r'\b' + old + r'\b', new, result, flags=re.IGNORECASE)
        
        return result
    
    def _simple_rephrase_content(self, content: str) -> str:
        sentences = re.split(r'[.!?]+', content)
        rephrased_sentences = []
        
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            
            rephrased = sentence
            
            word_replacements = {
                'сказал': 'отметил',
                'говорит': 'утверждает',
                'считает': 'полагает',
                'думает': 'считает',
                'планирует': 'намеревается',
                'хочет': 'планирует',
                'будет делать': 'планирует',
                'произошло': 'случилось',
                'случилось': 'имело место',
                'началось': 'стартовало',
                'закончилось': 'завершилось'
            }
            
            for old, new in word_replacements.items():
                rephrased = re.sub(r'\b' + old + r'\b', new, rephrased, flags=re.IGNORECASE)
            
            if 'в результате' in rephrased.lower():
                rephrased = rephrased.replace('В результате', 'Вследствие этого')
                rephrased = rephrased.replace('в результате', 'вследствие')
            
            if rephrased and not rephrased.endswith('.'):
                rephrased += '.'
            
            rephrased_sentences.append(rephrased)
        
        return ' '.join(rephrased_sentences)