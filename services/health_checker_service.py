import asyncio
import logging
from typing import Dict
from datetime import datetime

from core.interfaces import IHealthChecker, INewsRepository, IContentProcessor, INotificationService
from core.metrics import IMetricsCollector


class HealthCheckerService(IHealthChecker):
    def __init__(
        self,
        repository: INewsRepository,
        content_processor: IContentProcessor,
        notification_service: INotificationService,
        metrics: IMetricsCollector
    ):
        self.repository = repository
        self.content_processor = content_processor
        self.notification_service = notification_service
        self.metrics = metrics
        self.logger = logging.getLogger(__name__)
    
    async def check_health(self) -> Dict[str, bool]:
        self.logger.info("Starting health check")
        
        health_checks = {
            'database': self._check_database_health(),
            'content_processor': self._check_content_processor_health(),
            'notification_service': self._check_notification_service_health()
        }
        
        # Execute all health checks concurrently
        results = await asyncio.gather(*health_checks.values(), return_exceptions=True)
        
        health_status = {}
        check_names = list(health_checks.keys())
        
        for i, result in enumerate(results):
            check_name = check_names[i]
            
            if isinstance(result, Exception):
                self.logger.error(f"Health check {check_name} failed with exception: {result}")
                health_status[check_name] = False
                self.metrics.increment_counter("health_check.failed", {"service": check_name})
            else:
                health_status[check_name] = result
                if result:
                    self.metrics.increment_counter("health_check.passed", {"service": check_name})
                else:
                    self.metrics.increment_counter("health_check.failed", {"service": check_name})
        
        # Overall health status
        overall_healthy = all(health_status.values())
        health_status['overall'] = overall_healthy
        
        self.metrics.set_gauge("health_check.overall_status", 1.0 if overall_healthy else 0.0)
        
        if overall_healthy:
            self.logger.info("All health checks passed")
        else:
            failed_services = [name for name, status in health_status.items() if not status and name != 'overall']
            self.logger.warning(f"Health check failed for services: {', '.join(failed_services)}")
        
        return health_status
    
    async def _check_database_health(self) -> bool:
        try:
            # Try to get statistics to verify database connectivity
            stats = await self.repository.get_statistics()
            
            # Check if we got valid statistics
            if isinstance(stats, dict) and 'total_news' in stats:
                self.logger.debug("Database health check passed")
                return True
            else:
                self.logger.error("Database health check failed: invalid statistics response")
                return False
                
        except Exception as e:
            self.logger.error(f"Database health check failed: {e}")
            return False
    
    async def _check_content_processor_health(self) -> bool:
        try:
            is_available = await self.content_processor.is_available()
            
            if is_available:
                self.logger.debug("Content processor health check passed")
            else:
                self.logger.error("Content processor health check failed: service not available")
            
            return is_available
            
        except Exception as e:
            self.logger.error(f"Content processor health check failed: {e}")
            return False
    
    async def _check_notification_service_health(self) -> bool:
        try:
            is_available = await self.notification_service.is_available()
            
            if is_available:
                self.logger.debug("Notification service health check passed")
            else:
                self.logger.error("Notification service health check failed: service not available")
            
            return is_available
            
        except Exception as e:
            self.logger.error(f"Notification service health check failed: {e}")
            return False
    
    async def get_detailed_health_info(self) -> Dict[str, any]:
        health_status = await self.check_health()
        
        # Get additional system information
        try:
            db_stats = await self.repository.get_statistics()
        except Exception as e:
            self.logger.error(f"Failed to get database statistics: {e}")
            db_stats = {"error": str(e)}
        
        return {
            'timestamp': datetime.now().isoformat(),
            'health_status': health_status,
            'database_statistics': db_stats,
            'metrics_summary': self._get_metrics_summary()
        }
    
    def _get_metrics_summary(self) -> Dict[str, any]:
        try:
            if hasattr(self.metrics, 'get_all_metrics'):
                return self.metrics.get_all_metrics()
            else:
                return {"message": "Metrics summary not available"}
        except Exception as e:
            self.logger.error(f"Failed to get metrics summary: {e}")
            return {"error": str(e)}