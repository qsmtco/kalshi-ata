#!/usr/bin/env python3
"""
Circuit Breaker Monitor - Runs every 5 minutes to check safety conditions.

Per SAFETY_GUARDRAILS.md Section 3:
- Portfolio drawdown > 10% -> PAUSED_DRAWDOWN
- 24-hour loss > 5% -> PAUSED_DRAWDOWN  
- API error rate > 20% -> PAUSED_ERROR
- Auto-reset PAUSED_ERROR after 5 min if error rate < 5%

Usage: python3 safety_check.py [--base-url http://localhost:3050]
"""

import argparse
import logging
import sys
import os
from datetime import datetime, timedelta

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from safety_monitor import CircuitBreaker
from alert_manager import SafetyAlertManager
import config  # For TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_performance_metrics(base_url: str) -> dict:
    """Fetch current performance metrics from bot API."""
    import requests
    
    try:
        resp = requests.get(f"{base_url}/api/performance", timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except requests.RequestException as e:
        logger.error(f"Failed to fetch performance: {e}")
    
    return {}


def get_api_error_rate(base_url: str) -> float:
    """
    Calculate API error rate from last hour.
    Returns float between 0 and 1.
    
    For now, returns 0 (no errors) - in production would track from logs.
    """
    # TODO: Implement actual error rate tracking
    # For now, return 0 to allow normal operation
    return 0.0


def check_drawdown_and_loss(metrics: dict, cb: CircuitBreaker, bankroll: float) -> None:
    """
    Check drawdown and daily loss thresholds.
    Per Section 3.1: drawdown > 10% or 24h loss > 5% triggers PAUSED_DRAWDOWN.
    """
    if not metrics:
        logger.warning("No metrics available to check drawdown/loss")
        return
    
    # Extract metrics - adapt to actual API response format
    overall = metrics.get('overall_statistics', {})
    
    # Calculate current drawdown
    # Use total_pnl and initial bankroll to compute
    total_pnl = overall.get('total_pnl', 0)
    current_equity = bankroll + total_pnl
    drawdown = 0.0
    
    if bankroll > 0:
        drawdown = (bankroll - current_equity) / bankroll
        drawdown = max(0, drawdown)  # Only positive drawdown matters
    
    # 24h P&L - would need to filter trades by time
    # For now, use total_pnl as proxy (not accurate but demonstrates logic)
    daily_loss_pct = abs(total_pnl) / bankroll if total_pnl < 0 else 0
    
    logger.info(f"Current drawdown: {drawdown:.2%}, daily loss: {daily_loss_pct:.2%}")
    
    # Check thresholds from spec
    if drawdown > cb.DRAWDOWN_THRESHOLD:
        cb.pause_for_drawdown(f"Drawdown {drawdown:.1%} exceeded {cb.DRAWDOWN_THRESHOLD:.0%} threshold")
        return
    
    if daily_loss_pct > cb.DAILY_LOSS_THRESHOLD:
        cb.pause_for_drawdown(f"24h loss {daily_loss_pct:.1%} exceeded {cb.DAILY_LOSS_THRESHOLD:.0%} threshold")
        return


def check_api_errors(api_error_rate: float, cb: CircuitBreaker) -> None:
    """
    Check API error rate threshold.
    Per Section 3.1: error rate > 20% triggers PAUSED_ERROR.
    """
    logger.info(f"API error rate: {api_error_rate:.1%}")
    
    if cb.state == CircuitBreaker.CircuitState.ACTIVE:
        if api_error_rate > cb.API_ERROR_THRESHOLD:
            cb.pause_for_error(f"API error rate {api_error_rate:.1%} exceeded {cb.API_ERROR_THRESHOLD:.0%} threshold")
    elif cb.state == CircuitBreaker.CircuitState.PAUSED_ERROR:
        # Check for auto-reset conditions
        cb.check_auto_reset(api_error_rate)


def main():
    parser = argparse.ArgumentParser(description='Circuit Breaker Monitor')
    parser.add_argument(
        '--base-url', 
        default=os.environ.get('BOT_BASE_URL', 'http://localhost:3050'),
        help='Bot interface base URL'
    )
    parser.add_argument(
        '--bankroll',
        type=float,
        default=float(os.environ.get('BANKROLL', 1000)),
        help='Initial bankroll amount'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Log actions but do not modify circuit breaker state'
    )
    args = parser.parse_args()
    
    logger.info(f"Starting circuit breaker check at {datetime.now()}")
    logger.info(f"Target: {args.base_url}, bankroll: ${args.bankroll}")
    
    # Get current circuit breaker state
    cb = CircuitBreaker()
    status_before = cb.get_status()
    logger.info(f"Circuit state before: {status_before['state']}")
    
    # Fetch metrics
    metrics = get_performance_metrics(args.base_url)
    api_error_rate = get_api_error_rate(args.base_url)
    
    if args.dry_run:
        logger.info("DRY RUN - not modifying circuit breaker state")
        print(f"Would check: drawdown/loss, API error rate {api_error_rate:.1%}")
        print(f"Current state: {status_before['state']}")
        return
    
    # Run checks
    check_drawdown_and_loss(metrics, cb, args.bankroll)
    check_api_errors(api_error_rate, cb)
    
    # Get final state
    status_after = cb.get_status()
    
    # Initialize alert manager with Telegram credentials from config
    alert_manager = SafetyAlertManager()
    telegram_token = getattr(config, 'TELEGRAM_BOT_TOKEN', None)
    telegram_chat_id = getattr(config, 'TELEGRAM_CHAT_ID', None)
    
    if status_before['state'] != status_after['state']:
        logger.warning(f"CIRCUIT STATE CHANGED: {status_before['state']} -> {status_after['state']}")
        logger.warning(f"Reason: {status_after['reason']}")
        
        # Send alert via Telegram (rate-limited)
        alert_manager.notify_circuit_breaker(
            state=status_after['state'],
            reason=status_after['reason'],
            telegram_token=telegram_token,
            telegram_chat_id=telegram_chat_id
        )
        
        print(f"🚨 CIRCUIT BREAKER: {status_after['state']} - {status_after['reason']}")
    else:
        logger.info(f"Circuit state unchanged: {status_after['state']}")
    
    logger.info(f"Circuit breaker check complete at {datetime.now()}")


if __name__ == "__main__":
    main()
