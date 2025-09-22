import asyncio
import logging
from typing import Dict, List, Callable, Any
from collections import defaultdict
from .interfaces import IEventBus


class InMemoryEventBus(IEventBus):
    def __init__(self):
        self.handlers: Dict[str, List[Callable]] = defaultdict(list)
        self.logger = logging.getLogger(__name__)
    
    async def publish(self, event_type: str, data: Dict[str, Any]):
        self.logger.debug(f"Publishing event: {event_type} with data: {data}")
        
        handlers = self.handlers.get(event_type, [])
        if not handlers:
            self.logger.debug(f"No handlers registered for event: {event_type}")
            return
        
        # Execute all handlers concurrently
        tasks = []
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    tasks.append(handler(event_type, data))
                else:
                    # Run sync handler in thread pool
                    tasks.append(asyncio.get_event_loop().run_in_executor(
                        None, handler, event_type, data
                    ))
            except Exception as e:
                self.logger.error(f"Error creating task for handler {handler}: {e}")
        
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    self.logger.error(f"Handler {handlers[i]} failed: {result}")
    
    def subscribe(self, event_type: str, handler: Callable):
        self.handlers[event_type].append(handler)
        self.logger.debug(f"Handler {handler} subscribed to event: {event_type}")
    
    def unsubscribe(self, event_type: str, handler: Callable):
        if event_type in self.handlers:
            try:
                self.handlers[event_type].remove(handler)
                self.logger.debug(f"Handler {handler} unsubscribed from event: {event_type}")
            except ValueError:
                self.logger.warning(f"Handler {handler} not found for event: {event_type}")
    
    def get_handlers_count(self, event_type: str) -> int:
        return len(self.handlers.get(event_type, []))
    
    def clear_handlers(self, event_type: str = None):
        if event_type:
            self.handlers[event_type].clear()
            self.logger.debug(f"Cleared handlers for event: {event_type}")
        else:
            self.handlers.clear()
            self.logger.debug("Cleared all event handlers")