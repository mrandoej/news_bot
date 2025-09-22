from abc import ABC, abstractmethod
from typing import List, Optional
import re
from .interfaces import NewsItem, NewsValidationError


class INewsValidator(ABC):
    @abstractmethod
    def validate(self, news: NewsItem) -> Optional[str]:
        pass


class TitleValidator(INewsValidator):
    def __init__(self, min_length: int = 10, max_length: int = 200):
        self.min_length = min_length
        self.max_length = max_length
    
    def validate(self, news: NewsItem) -> Optional[str]:
        if not news.title or not news.title.strip():
            return "Title cannot be empty"
        
        title_length = len(news.title.strip())
        if title_length < self.min_length:
            return f"Title too short: {title_length} < {self.min_length}"
        
        if title_length > self.max_length:
            return f"Title too long: {title_length} > {self.max_length}"
        
        return None


class ContentValidator(INewsValidator):
    def __init__(self, min_length: int = 20, max_length: int = 5000):
        self.min_length = min_length
        self.max_length = max_length
    
    def validate(self, news: NewsItem) -> Optional[str]:
        if not news.content or not news.content.strip():
            return "Content cannot be empty"
        
        content_length = len(news.content.strip())
        if content_length < self.min_length:
            return f"Content too short: {content_length} < {self.min_length}"
        
        if content_length > self.max_length:
            return f"Content too long: {content_length} > {self.max_length}"
        
        return None


class UrlValidator(INewsValidator):
    def __init__(self):
        self.url_pattern = re.compile(
            r'^https?://'  # http:// or https://
            r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'  # domain...
            r'localhost|'  # localhost...
            r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # ...or ip
            r'(?::\d+)?'  # optional port
            r'(?:/?|[/?]\S+)$', re.IGNORECASE)
    
    def validate(self, news: NewsItem) -> Optional[str]:
        if not news.url:
            return None  # URL is optional
        
        if not self.url_pattern.match(news.url):
            return f"Invalid URL format: {news.url}"
        
        return None


class RegionKeywordValidator(INewsValidator):
    def __init__(self, keywords: List[str], exclude_keywords: List[str] = None):
        self.keywords = [kw.lower() for kw in keywords]
        self.exclude_keywords = [kw.lower() for kw in (exclude_keywords or [])]
        
        # Ambiguous keywords that need context validation
        self.ambiguous_keywords = {
            'пугачев': {
                'geographic_context': ['город', 'районе', 'области', 'муниципальный', 'администрация', 'мэр', 'жители'],
                'person_context': ['пугачева', 'алла', 'певица', 'артистка', 'интервью', 'концерт', 'песня', 'госдума', 'депутат']
            },
            'маркс': {
                'geographic_context': ['город', 'районе', 'области', 'муниципальный', 'администрация', 'мэр', 'жители'],
                'person_context': ['карл', 'философ', 'капитал', 'коммунизм', 'марксизм', 'теория']
            },
            'энгельс': {
                'geographic_context': ['город', 'районе', 'области', 'муниципальный', 'администрация', 'мэр', 'жители'],
                'person_context': ['фридрих', 'философ', 'коммунизм', 'маркс', 'теория']
            }
        }
    
    def validate(self, news: NewsItem) -> Optional[str]:
        text = f"{news.title} {news.content}".lower()
        
        # Check for excluded keywords first
        for exclude_word in self.exclude_keywords:
            if exclude_word in text:
                return f"News excluded due to keyword: {exclude_word}"
        
        # Check for required regional keywords
        found_keywords = []
        for keyword in self.keywords:
            if keyword in text:
                if self._is_valid_regional_keyword(keyword, text):
                    found_keywords.append(keyword)
        
        if not found_keywords:
            return "No relevant regional keywords found"
        
        return None
    
    def _is_valid_regional_keyword(self, keyword: str, text: str) -> bool:
        if keyword not in self.ambiguous_keywords:
            return True
        
        ambiguous_data = self.ambiguous_keywords[keyword]
        
        # Check for geographic context
        geographic_found = any(geo_word in text for geo_word in ambiguous_data['geographic_context'])
        
        # Check for personal context
        person_found = any(person_word in text for person_word in ambiguous_data['person_context'])
        
        # If geographic context found and no personal context - accept
        if geographic_found and not person_found:
            return True
        
        # If personal context found - reject
        if person_found:
            return False
        
        # If context unclear but other regional words present - accept
        if any(regional_word in text for regional_word in ['саратов', 'саратовская область', 'саратовский']):
            return True
        
        # If context unclear and no other regional words - reject
        return False


class NewsValidationChain:
    def __init__(self, validators: List[INewsValidator]):
        self.validators = validators
    
    def validate(self, news: NewsItem) -> None:
        errors = []
        
        for validator in self.validators:
            error = validator.validate(news)
            if error:
                errors.append(error)
        
        if errors:
            raise NewsValidationError(f"Validation failed: {'; '.join(errors)}")
    
    def is_valid(self, news: NewsItem) -> bool:
        try:
            self.validate(news)
            return True
        except NewsValidationError:
            return False