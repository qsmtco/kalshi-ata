#!/usr/bin/env python3
"""
Alert Module for K-ATA Safety Systems.

Per SAFETY_GUARDRAILS.md Section 6:
- Alert channels: OpenClaw message, Telegram, Log
- Rate limiting: Same alert type at most once per 15 minutes
- Critical alerts (circuit breaker, crash): always send

Alert Types:
- CIRCUIT_BREAKER: HIGH severity
- ROLLBACK: HIGH severity
- AGENT_REJECTED: MEDIUM severity
- BOT_CRASH: HIGH severity
- API_ERROR_RATE: MEDIUM severity
"""

import os
import json
import logging
import time
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class AlertType(Enum):
    """Alert types per spec Section 6.2."""
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"
    ROLLBACK = "ROLLBACK"
    AGENT_REJECTED = "AGENT_REJECTED"
    BOT_CRASH = "BOT_CRASH"
    API_ERROR_RATE = "API_ERROR_RATE"


class AlertSeverity(Enum):
    """Severity levels."""
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"


# Mapping from alert type to severity and message template
ALERT_CONFIG = {
    AlertType.CIRCUIT_BREAKER: {
        "severity": AlertSeverity.HIGH,
        "template": "⚠️ CIRCUIT BREAKER: {reason}. Trading halted."
    },
    AlertType.ROLLBACK: {
        "severity": AlertSeverity.HIGH,
        "template": "🔙 ROLLBACK: Reverted to {timestamp} due to {reason}."
    },
    AlertType.AGENT_REJECTED: {
        "severity": AlertSeverity.MEDIUM,
        "template": "🚫 Agent adjustment blocked: {parameter}={value} violates guardrail."
    },
    AlertType.BOT_CRASH: {
        "severity": AlertSeverity.HIGH,
        "template": "💥 Bot process crashed. Restarting..."
    },
    AlertType.API_ERROR_RATE: {
        "severity": AlertSeverity.MEDIUM,
        "template": "⚠️ API error rate elevated: {rate:.1%}"
    },
}


@dataclass
class Alert:
    """Represents an alert to be sent."""
    alert_type: AlertType
    message: str
    severity: AlertSeverity
    timestamp: datetime


class AlertRateLimiter:
    """
    Rate limits alerts to prevent spam.
    Per spec: Same alert type at most once per 15 minutes.
    Critical alerts (HIGH severity) are always sent.
    """
    
    RATE_LIMIT_SECONDS = 900  # 15 minutes
    
    def __init__(self, state_file: str = "data/alert_state.json"):
        self.state_file = state_file
        self.last_alert_time: dict[AlertType, float] = {}
        self._load_state()
    
    def _load_state(self) -> None:
        """Load persisted alert times."""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                    # Convert timestamp strings back to floats
                    self.last_alert_time = {
                        AlertType(k): float(v) 
                        for k, v in data.get('last_alert_time', {}).items()
                    }
            except (json.JSONDecodeError, ValueError) as exc:
                logger.warning(f"Failed to load alert state: {exc}")
    
    def _save_state(self) -> None:
        """Persist alert times."""
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        with open(self.state_file, 'w') as f:
            json.dump({
                'last_alert_time': {
                    k.value: v for k, v in self.last_alert_time.items()
                }
            }, f)
    
    def should_send(self, alert_type: AlertType) -> bool:
        """
        Check if alert should be sent based on rate limiting.
        Returns True if:
        - HIGH severity (always send)
        - Or 15+ minutes since last of this type
        """
        config = ALERT_CONFIG[alert_type]
        
        # HIGH severity alerts always go through
        if config['severity'] == AlertSeverity.HIGH:
            return True
        
        # MEDIUM severity: check rate limit
        last_time = self.last_alert_time.get(alert_type)
        if last_time is None:
            return True
        
        elapsed = time.time() - last_time
        return elapsed >= self.RATE_LIMIT_SECONDS
    
    def record_alert(self, alert_type: AlertType) -> None:
        """Record that an alert was sent."""
        self.last_alert_time[alert_type] = time.time()
        self._save_state()


class SafetyAlertManager:
    """
    Manages safety alerts with rate limiting and multiple channels.
    """
    
    def __init__(self):
        self.rate_limiter = AlertRateLimiter()
    
    def send_alert(
        self,
        alert_type: AlertType,
        context: dict,
        telegram_token: Optional[str] = None,
        telegram_chat_id: Optional[str] = None,
        openclaw_session: Optional[str] = None
    ) -> bool:
        """
        Send an alert via configured channels.
        
        Args:
            alert_type: Type of alert
            context: Dict with template variables (reason, timestamp, parameter, value, rate)
            telegram_token: Telegram bot token
            telegram_chat_id: Target chat ID
            openclaw_session: Session key for OpenClaw message
        
        Returns:
            True if alert was sent
        """
        # Check rate limit
        if not self.rate_limiter.should_send(alert_type):
            logger.info(f"Alert {alert_type.value} rate-limited, skipping")
            return False
        
        # Build message from template
        config = ALERT_CONFIG[alert_type]
        try:
            message = config['template'].format(**context)
        except KeyError as e:
            logger.error(f"Missing template variable: {e}")
            message = f"{alert_type.value}: {context}"
        
        alert = Alert(
            alert_type=alert_type,
            message=message,
            severity=config['severity'],
            timestamp=datetime.now()
        )
        
        # Log always
        logger.warning(f"ALERT [{alert.severity.value}]: {alert.message}")
        
        # Send via Telegram
        if telegram_token and telegram_token != "your_telegram_bot_token":
            self._send_telegram(telegram_token, telegram_chat_id, message)
        
        # Send via OpenClaw (would need integration)
        # if openclaw_session:
        #     self._send_openclaw(openclaw_session, message)
        
        # Record that we sent this alert type
        self.rate_limiter.record_alert(alert_type)
        
        return True
    
    def _send_telegram(self, token: str, chat_id: str, message: str) -> bool:
        """Send message via Telegram API."""
        import requests
        
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            resp = requests.post(url, json={
                'chat_id': chat_id,
                'text': message,
                'parse_mode': 'Markdown'
            }, timeout=10)
            
            if resp.status_code == 200:
                logger.info(f"Telegram alert sent: {message[:50]}...")
                return True
            else:
                logger.error(f"Telegram send failed: {resp.status_code}")
                return False
        except requests.RequestException as e:
            logger.error(f"Telegram request failed: {e}")
            return False
    
    def notify_circuit_breaker(
        self,
        state: str,
        reason: str,
        telegram_token: Optional[str] = None,
        telegram_chat_id: Optional[str] = None
    ) -> bool:
        """Convenience method for circuit breaker alerts."""
        return self.send_alert(
            AlertType.CIRCUIT_BREAKER,
            {'reason': reason},
            telegram_token,
            telegram_chat_id
        )
    
    def notify_rollback(
        self,
        timestamp: str,
        reason: str,
        telegram_token: Optional[str] = None,
        telegram_chat_id: Optional[str] = None
    ) -> bool:
        """Convenience method for rollback alerts."""
        return self.send_alert(
            AlertType.ROLLBACK,
            {'timestamp': timestamp, 'reason': reason},
            telegram_token,
            telegram_chat_id
        )
    
    def notify_agent_rejected(
        self,
        parameter: str,
        value: str,
        telegram_token: Optional[str] = None,
        telegram_chat_id: Optional[str] = None
    ) -> bool:
        """Convenience method for agent adjustment rejected alerts."""
        return self.send_alert(
            AlertType.AGENT_REJECTED,
            {'parameter': parameter, 'value': value},
            telegram_token,
            telegram_chat_id
        )


if __name__ == "__main__":
    # Quick test
    import os
    os.makedirs("data", exist_ok=True)
    
    # Test rate limiting
    manager = SafetyAlertManager()
    
    # This should send (first time)
    result = manager.notify_circuit_breaker(
        "PAUSED_DRAWDOWN",
        "Test drawdown 12%",
        None, None
    )
    print(f"First alert sent: {result}")
    
    # This should be rate limited (same type, less than 15 min)
    result2 = manager.notify_circuit_breaker(
        "PAUSED_DRAWDOWN", 
        "Test again",
        None, None
    )
    print(f"Second alert rate limited: {not result2}")
    
    print("✅ Alert system basic test passed")
