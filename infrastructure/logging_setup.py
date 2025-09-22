import logging
import logging.handlers
import os
import sys
from typing import Dict, Any
from infrastructure.config_manager import LoggingConfig


class StructuredFormatter(logging.Formatter):
    def format(self, record):
        # Add structured fields to log record
        if not hasattr(record, 'component'):
            record.component = record.name.split('.')[-1]
        
        if not hasattr(record, 'operation'):
            record.operation = getattr(record, 'funcName', 'unknown')
        
        # Format the message
        formatted = super().format(record)
        
        # Add context if available
        if hasattr(record, 'context'):
            formatted += f" | Context: {record.context}"
        
        return formatted


class LoggingSetup:
    @staticmethod
    def setup_logging(config: LoggingConfig) -> None:
        # Create logs directory if it doesn't exist
        log_dir = os.path.dirname(config.file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        
        # Create root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(getattr(logging, config.level.upper()))
        
        # Clear existing handlers
        root_logger.handlers.clear()
        
        # Create formatter
        formatter = StructuredFormatter(config.format)
        
        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(getattr(logging, config.level.upper()))
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)
        
        # File handler with rotation
        file_handler = logging.handlers.RotatingFileHandler(
            config.file,
            maxBytes=config.max_file_size,
            backupCount=config.backup_count,
            encoding='utf-8'
        )
        file_handler.setLevel(getattr(logging, config.level.upper()))
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
        
        # Reduce noise from third-party libraries
        logging.getLogger('urllib3').setLevel(logging.WARNING)
        logging.getLogger('requests').setLevel(logging.WARNING)
        logging.getLogger('telegram').setLevel(logging.WARNING)
        logging.getLogger('aiohttp').setLevel(logging.WARNING)
        
        logging.info("Logging system initialized", extra={'component': 'logging'})
    
    @staticmethod
    def get_logger(name: str) -> logging.Logger:
        return logging.getLogger(name)
    
    @staticmethod
    def log_with_context(logger: logging.Logger, level: int, message: str, **context):
        extra = {'context': context} if context else {}
        logger.log(level, message, extra=extra)