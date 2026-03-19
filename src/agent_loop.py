#!/usr/bin/env python3
"""
Main Agent Loop for K-ATA Adaptive Agent.

Per AGENT_LOGIC.md Section 7:
Orchestrates the full agent cycle:
1. Fetch data (performance, trades, settings)
2. Check circuit breaker
3. Generate hypotheses
4. Backtest each hypothesis
5. Compute adjustments (50% step)
6. Validate guardrails
7. Apply via API
8. Log decisions

Usage: python3 agent_loop.py [--base-url http://localhost:3001]
"""

import argparse
import logging
import sys
import os
import requests
from datetime import datetime

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from safety_monitor import CircuitBreaker
from hypothesis_generator import HypothesisGenerator
from backtester import Backtester
from agent_decisions import AgentDecisionLogger

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# Guardrails from SAFETY_GUARDRAILS.md Section 2
GUARDRAILS = {
    'kellyFraction': (0.1, 0.8),
    'maxPositionSizePct': (0.01, 0.25),
    'stopLossPct': (0.01, 0.20),
    'takeProfitPct': (0.02, 0.50),
    'newsSentimentThreshold': (0.3, 0.9),
    'statArbitrageThreshold': (0.01, 0.20),
    'volatilityThreshold': (0.05, 0.30),
    'tradeIntervalSeconds': (30, 3600),
}


def fetch_performance(base_url: str) -> dict:
    """Fetch current performance metrics from bot API."""
    try:
        resp = requests.get(f"{base_url}/api/performance", timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except requests.RequestException as e:
        logger.error(f"Failed to fetch performance: {e}")
    return {}


def fetch_settings(base_url: str) -> dict:
    """Fetch current settings from bot API."""
    try:
        resp = requests.get(f"{base_url}/api/settings", timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except requests.RequestException as e:
        logger.error(f"Failed to fetch settings: {e}")
    return {}


def fetch_trades(backtester: Backtester, limit: int = 100) -> list:
    """Fetch recent trades from database."""
    return backtester.get_trades(limit=limit)


def check_circuit_breaker(cb: CircuitBreaker) -> bool:
    """Check if circuit breaker allows trading."""
    status = cb.get_status()
    if not status['can_trade']:
        logger.warning(f"Circuit breaker active: {status['state']} - {status['reason']}")
        return False
    return True


def check_rate_limit(decision_logger: AgentDecisionLogger, max_per_day: int = 3) -> bool:
    """Check if rate limit exceeded (per Section 6)."""
    count = decision_logger.get_decisions_count_last_24h()
    if count >= max_per_day:
        logger.warning(f"Rate limit exceeded: {count} decisions in last 24h")
        return False
    return True


def validate_guardrail(parameter: str, value: float) -> tuple:
    """Validate value against guardrails. Returns (valid, error_msg)."""
    if parameter not in GUARDRAILS:
        return True, ""
    
    min_val, max_val = GUARDRAILS[parameter]
    if not (min_val <= value <= max_val):
        return False, f"Value {value} outside [{min_val}, {max_val}]"
    return True, ""


def compute_conservative_adjustment(current: float, suggested: float) -> float:
    """Per Section 4.1: Take 50% step toward suggested value."""
    return current + (suggested - current) * 0.5


def apply_adjustment(base_url: str, parameter: str, value: float) -> bool:
    """Apply setting change via API (per Section 5.1)."""
    try:
        resp = requests.post(
            f"{base_url}/api/settings",
            json={parameter: value},
            timeout=10
        )
        if resp.status_code == 200:
            logger.info(f"Applied {parameter}={value}")
            return True
        else:
            logger.error(f"Failed to apply {parameter}: {resp.status_code}")
            return False
    except requests.RequestException as e:
        logger.error(f"API error applying {parameter}: {e}")
        return False


def run_agent_cycle(base_url: str, dry_run: bool = False) -> dict:
    """
    Run one complete agent cycle.
    Returns summary of what happened.
    """
    logger.info(f"Starting agent cycle at {datetime.now()}")
    
    summary = {
        'hypotheses_generated': 0,
        'hypotheses_tested': 0,
        'adjustments_applied': 0,
        'adjustments_rejected': 0,
        'errors': []
    }
    
    # Step 1: Initialize components
    decision_logger = AgentDecisionLogger()
    circuit_breaker = CircuitBreaker()
    backtester = Backtester()
    
    # Step 2: Check circuit breaker
    if not check_circuit_breaker(circuit_breaker):
        decision_logger.log_decision(
            decision_type='circuit_breaker',
            rationale='Skipped cycle - circuit breaker active',
            applied=False
        )
        summary['errors'].append('circuit_breaker_active')
        return summary
    
    # Step 3: Check rate limit
    if not check_rate_limit(decision_logger):
        decision_logger.log_decision(
            decision_type='hypothesis_generated',
            rationale='Skipped - rate limit exceeded',
            applied=False
        )
        summary['errors'].append('rate_limit_exceeded')
        return summary
    
    # Step 4: Fetch data
    performance = fetch_performance(base_url)
    settings = fetch_settings(base_url)
    trades = fetch_trades(backtester)
    
    logger.info(f"Fetched performance, {len(trades)} trades, settings")
    
    # Step 5: Generate hypotheses
    generator = HypothesisGenerator(settings)
    hypotheses = generator.generate(performance)
    summary['hypotheses_generated'] = len(hypotheses)
    
    logger.info(f"Generated {len(hypotheses)} hypotheses")
    
    # Log generated hypotheses
    for hyp in hypotheses:
        decision_logger.log_decision(
            decision_type='hypothesis_generated',
            rationale=f"Generated: {hyp.hypothesis_type} for {hyp.parameter}",
            parameters_modified={'parameter': hyp.parameter, 'suggested': hyp.suggested_value},
            hypothesis_tested=f"{hyp.hypothesis_type}_{hyp.parameter}",
            applied=False
        )
    
    # Step 6: Backtest and apply each hypothesis
    for hyp in hypotheses:
        summary['hypotheses_tested'] += 1
        
        # Backtest
        result = backtester.backtest(hyp.to_dict(), trades)
        logger.info(f"Backtest for {hyp.parameter}: accepted={result.accepted}, {result.reason}")
        
        if not result.accepted:
            logger.info(f"Hypothesis rejected: {result.reason}")
            decision_logger.log_decision(
                decision_type='parameter_tuning',
                rationale=f"Rejected: {result.reason}",
                parameters_modified={'parameter': hyp.parameter, 'suggested': hyp.suggested_value},
                hypothesis_tested=hyp.hypothesis_type,
                p_value=result.p_value,
                effect_size=result.effect_size,
                applied=False
            )
            summary['adjustments_rejected'] += 1
            continue
        
        # Step 7: Compute conservative adjustment
        current = hyp.current_value
        suggested = hyp.suggested_value
        adjusted = compute_conservative_adjustment(current, suggested)
        
        # Step 8: Validate guardrail
        valid, error_msg = validate_guardrail(hyp.parameter, adjusted)
        if not valid:
            logger.warning(f"Guardrail violation: {error_msg}")
            decision_logger.log_decision(
                decision_type='parameter_tuning',
                rationale=f"Rejected by guardrail: {error_msg}",
                parameters_modified={'parameter': hyp.parameter, 'suggested': adjusted},
                hypothesis_tested=hyp.hypothesis_type,
                applied=False
            )
            summary['adjustments_rejected'] += 1
            continue
        
        # Step 9: Apply if not dry run
        if dry_run:
            logger.info(f"DRY RUN: Would apply {hyp.parameter}={adjusted}")
        else:
            success = apply_adjustment(base_url, hyp.parameter, adjusted)
            if success:
                summary['adjustments_applied'] += 1
            else:
                summary['adjustments_rejected'] += 1
                continue
        
        # Log successful application
        decision_logger.log_decision(
            decision_type='parameter_tuning',
            rationale=f"Applied: {current} -> {adjusted} ({hyp.rationale})",
            parameters_modified={hyp.parameter: adjusted},
            hypothesis_tested=hyp.hypothesis_type,
            p_value=result.p_value,
            effect_size=result.effect_size,
            metrics_before={hyp.parameter: current},
            metrics_after={hyp.parameter: adjusted},
            applied=not dry_run
        )
    
    logger.info(f"Agent cycle complete: {summary}")
    return summary


def main():
    parser = argparse.ArgumentParser(description='K-ATA Adaptive Agent')
    parser.add_argument(
        '--base-url',
        default=os.environ.get('BOT_BASE_URL', 'http://localhost:3001'),
        help='Bot interface base URL'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Log actions but do not apply changes'
    )
    args = parser.parse_args()
    
    logger.info(f"Starting adaptive agent (base_url={args.base_url}, dry_run={args.dry_run})")
    
    summary = run_agent_cycle(args.base_url, dry_run=args.dry_run)
    
    print(f"\n=== Agent Cycle Summary ===")
    print(f"Hypotheses generated: {summary['hypotheses_generated']}")
    print(f"Hypotheses tested: {summary['hypotheses_tested']}")
    print(f"Adjustments applied: {summary['adjustments_applied']}")
    print(f"Adjustments rejected: {summary['adjustments_rejected']}")
    if summary['errors']:
        print(f"Errors: {summary['errors']}")


if __name__ == "__main__":
    main()
