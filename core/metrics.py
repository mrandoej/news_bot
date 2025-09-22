import time
import logging
from typing import Dict, Optional, DefaultDict
from collections import defaultdict
from .interfaces import IMetricsCollector


class InMemoryMetricsCollector(IMetricsCollector):
    def __init__(self):
        self.counters: DefaultDict[str, int] = defaultdict(int)
        self.gauges: Dict[str, float] = {}
        self.durations: DefaultDict[str, list] = defaultdict(list)
        self.logger = logging.getLogger(__name__)
    
    def increment_counter(self, metric: str, tags: Dict[str, str] = None):
        key = self._build_key(metric, tags)
        self.counters[key] += 1
        self.logger.debug(f"Counter incremented: {key} = {self.counters[key]}")
    
    def record_duration(self, metric: str, duration: float, tags: Dict[str, str] = None):
        key = self._build_key(metric, tags)
        self.durations[key].append(duration)
        self.logger.debug(f"Duration recorded: {key} = {duration:.3f}s")
    
    def set_gauge(self, metric: str, value: float, tags: Dict[str, str] = None):
        key = self._build_key(metric, tags)
        self.gauges[key] = value
        self.logger.debug(f"Gauge set: {key} = {value}")
    
    def get_counter(self, metric: str, tags: Dict[str, str] = None) -> int:
        key = self._build_key(metric, tags)
        return self.counters.get(key, 0)
    
    def get_gauge(self, metric: str, tags: Dict[str, str] = None) -> Optional[float]:
        key = self._build_key(metric, tags)
        return self.gauges.get(key)
    
    def get_duration_stats(self, metric: str, tags: Dict[str, str] = None) -> Dict[str, float]:
        key = self._build_key(metric, tags)
        durations = self.durations.get(key, [])
        
        if not durations:
            return {}
        
        return {
            'count': len(durations),
            'min': min(durations),
            'max': max(durations),
            'avg': sum(durations) / len(durations),
            'total': sum(durations)
        }
    
    def _build_key(self, metric: str, tags: Dict[str, str] = None) -> str:
        if not tags:
            return metric
        
        tag_str = ','.join(f"{k}={v}" for k, v in sorted(tags.items()))
        return f"{metric}[{tag_str}]"
    
    def get_all_metrics(self) -> Dict[str, any]:
        return {
            'counters': dict(self.counters),
            'gauges': dict(self.gauges),
            'durations': {k: self.get_duration_stats(k.split('[')[0], 
                                                   self._parse_tags(k)) 
                         for k in self.durations.keys()}
        }
    
    def _parse_tags(self, key: str) -> Optional[Dict[str, str]]:
        if '[' not in key:
            return None
        
        tag_part = key.split('[')[1].rstrip(']')
        if not tag_part:
            return None
        
        tags = {}
        for tag in tag_part.split(','):
            if '=' in tag:
                k, v = tag.split('=', 1)
                tags[k] = v
        
        return tags if tags else None


def timed_metric(metrics_getter, metric_name: str, tags: Dict[str, str] = None):
    def decorator(func):
        async def async_wrapper(*args, **kwargs):
            # Get metrics instance from the first argument (self)
            metrics = metrics_getter(args[0]) if callable(metrics_getter) else metrics_getter
            start_time = time.time()
            try:
                result = await func(*args, **kwargs)
                metrics.increment_counter(f"{metric_name}.success", tags)
                return result
            except Exception as e:
                metrics.increment_counter(f"{metric_name}.error", tags)
                raise
            finally:
                duration = time.time() - start_time
                metrics.record_duration(metric_name, duration, tags)
        
        def sync_wrapper(*args, **kwargs):
            # Get metrics instance from the first argument (self)
            metrics = metrics_getter(args[0]) if callable(metrics_getter) else metrics_getter
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                metrics.increment_counter(f"{metric_name}.success", tags)
                return result
            except Exception as e:
                metrics.increment_counter(f"{metric_name}.error", tags)
                raise
            finally:
                duration = time.time() - start_time
                metrics.record_duration(metric_name, duration, tags)
        
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator