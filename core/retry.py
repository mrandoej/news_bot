import asyncio
import random
import time
import logging
from typing import Callable, Any, Optional, Type, Union
from functools import wraps


class RetryConfig:
    def __init__(
        self,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
        jitter: bool = True,
        exceptions: tuple = (Exception,)
    ):
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter
        self.exceptions = exceptions


def retry_async(config: RetryConfig):
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            logger = logging.getLogger(func.__module__)
            last_exception = None
            
            for attempt in range(config.max_attempts):
                try:
                    return await func(*args, **kwargs)
                except config.exceptions as e:
                    last_exception = e
                    
                    if attempt == config.max_attempts - 1:
                        logger.error(f"Function {func.__name__} failed after {config.max_attempts} attempts: {e}")
                        raise e
                    
                    delay = min(
                        config.base_delay * (config.exponential_base ** attempt),
                        config.max_delay
                    )
                    
                    if config.jitter:
                        delay *= (0.5 + random.random() * 0.5)
                    
                    logger.warning(f"Function {func.__name__} failed on attempt {attempt + 1}, retrying in {delay:.2f}s: {e}")
                    await asyncio.sleep(delay)
            
            raise last_exception
        
        return wrapper
    return decorator


def retry_sync(config: RetryConfig):
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            logger = logging.getLogger(func.__module__)
            last_exception = None
            
            for attempt in range(config.max_attempts):
                try:
                    return func(*args, **kwargs)
                except config.exceptions as e:
                    last_exception = e
                    
                    if attempt == config.max_attempts - 1:
                        logger.error(f"Function {func.__name__} failed after {config.max_attempts} attempts: {e}")
                        raise e
                    
                    delay = min(
                        config.base_delay * (config.exponential_base ** attempt),
                        config.max_delay
                    )
                    
                    if config.jitter:
                        delay *= (0.5 + random.random() * 0.5)
                    
                    logger.warning(f"Function {func.__name__} failed on attempt {attempt + 1}, retrying in {delay:.2f}s: {e}")
                    time.sleep(delay)
            
            raise last_exception
        
        return wrapper
    return decorator


def smart_retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
    jitter: bool = True,
    exceptions: tuple = (Exception,)
):
    config = RetryConfig(max_attempts, base_delay, max_delay, exponential_base, jitter, exceptions)
    
    def decorator(func: Callable) -> Callable:
        if asyncio.iscoroutinefunction(func):
            return retry_async(config)(func)
        else:
            return retry_sync(config)(func)
    
    return decorator