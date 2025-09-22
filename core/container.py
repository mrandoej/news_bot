from typing import Dict, Any, TypeVar, Type, Callable, Optional
import inspect
from functools import wraps

T = TypeVar('T')


class DIContainer:
    def __init__(self):
        self._services: Dict[str, Any] = {}
        self._factories: Dict[str, Callable] = {}
        self._singletons: Dict[str, Any] = {}
        
    def register_singleton(self, interface: Type[T], implementation: Type[T]) -> 'DIContainer':
        key = self._get_key(interface)
        self._factories[key] = implementation
        return self
    
    def register_transient(self, interface: Type[T], implementation: Type[T]) -> 'DIContainer':
        key = self._get_key(interface)
        self._factories[key] = implementation
        return self
    
    def register_instance(self, interface: Type[T], instance: T) -> 'DIContainer':
        key = self._get_key(interface)
        self._singletons[key] = instance
        return self
    
    def register_factory(self, interface: Type[T], factory: Callable[[], T]) -> 'DIContainer':
        key = self._get_key(interface)
        self._factories[key] = factory
        return self
    
    def resolve(self, interface: Type[T]) -> T:
        key = self._get_key(interface)
        
        # Check if singleton instance exists
        if key in self._singletons:
            return self._singletons[key]
        
        # Check if factory exists
        if key in self._factories:
            factory = self._factories[key]
            
            # If it's a class, create instance with dependency injection
            if inspect.isclass(factory):
                instance = self._create_instance(factory)
                # Store as singleton if registered as such
                if key in self._factories:
                    self._singletons[key] = instance
                return instance
            else:
                # It's a factory function
                return factory()
        
        raise ValueError(f"Service {interface.__name__} not registered")
    
    def _create_instance(self, cls: Type[T]) -> T:
        # Get constructor signature
        sig = inspect.signature(cls.__init__)
        params = {}
        
        for param_name, param in sig.parameters.items():
            if param_name == 'self':
                continue
                
            if param.annotation != inspect.Parameter.empty:
                # Resolve dependency
                params[param_name] = self.resolve(param.annotation)
        
        return cls(**params)
    
    def _get_key(self, interface: Type) -> str:
        return f"{interface.__module__}.{interface.__name__}"


def inject(container: DIContainer):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            sig = inspect.signature(func)
            injected_kwargs = {}
            
            for param_name, param in sig.parameters.items():
                if param_name not in kwargs and param.annotation != inspect.Parameter.empty:
                    try:
                        injected_kwargs[param_name] = container.resolve(param.annotation)
                    except ValueError:
                        pass  # Parameter not registered, skip injection
            
            return func(*args, **kwargs, **injected_kwargs)
        return wrapper
    return decorator