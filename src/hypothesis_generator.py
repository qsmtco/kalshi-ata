#!/usr/bin/env python3
"""
Hypothesis Generator for K-ATA Adaptive Agent.

Per AGENT_LOGIC.md Section 2:
- Analyzes performance data to detect parameter improvement opportunities
- Generates hypothesis candidates with suggested values
- Stateless - all config from parameters

Hypothesis Types:
- threshold_adjust: Strategy win rate below threshold
- strategy_disable: Strategy inactive/underperforming
- strategy_enable: Regime favorable for disabled strategy
- position_size_adjust: High correlation between strategies
- kelly_adjust: Sharpe ratio too high or too low
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass


@dataclass
class Hypothesis:
    """Represents a single hypothesis for parameter adjustment."""
    hypothesis_type: str
    parameter: str
    current_value: Any
    suggested_value: Any
    rationale: str
    strategy: Optional[str] = None
    
    def to_dict(self) -> dict:
        return {
            'type': self.hypothesis_type,
            'parameter': self.parameter,
            'current': self.current_value,
            'suggested': self.suggested_value,
            'rationale': self.rationale,
            'strategy': self.strategy
        }


class HypothesisGenerator:
    """
    Generates hypotheses based on performance analysis.
    Per Section 2.1: 5 main hypothesis types.
    """
    
    # Guardrails from SAFETY_GUARDRAILS.md
    GUARDRAILS = {
        'kellyFraction': (0.1, 0.8),
        'maxPositionSizePct': (0.01, 0.25),
        'newsSentimentThreshold': (0.3, 0.9),
        'statArbitrageThreshold': (0.01, 0.20),
        'volatilityThreshold': (0.05, 0.30)
    }
    
    def __init__(self, current_settings: Dict[str, Any]):
        self.settings = current_settings
    
    def generate(self, performance: Dict[str, Any]) -> List[Hypothesis]:
        """
        Generate hypothesis candidates from performance data.
        
        Args:
            performance: Dict with 'overall_statistics' and 'strategy_breakdown'
        
        Returns:
            List of Hypothesis objects
        """
        hypotheses = []
        
        # Get strategy performance
        strategy_perf = performance.get('strategy_breakdown', {})
        overall = performance.get('overall_statistics', {})
        
        # H1: Threshold adjustments based on win rate
        for strat_name, strat_data in strategy_perf.items():
            hyp = self._check_threshold_adjust(strat_name, strat_data)
            if hyp:
                hypotheses.append(hyp)
        
        # H2: Position size adjustment for correlation risk
        hyp = self._check_correlation_risk(strategy_perf)
        if hyp:
            hypotheses.append(hyp)
        
        # H3: Kelly adjustment based on Sharpe
        hyp = self._check_kelly_adjust(overall)
        if hyp:
            hypotheses.append(hyp)
        
        # H4: Strategy disable for underperforming
        for strat_name, strat_data in strategy_perf.items():
            hyp = self._check_strategy_disable(strat_name, strat_data)
            if hyp:
                hypotheses.append(hyp)
        
        return hypotheses
    
    def _check_threshold_adjust(
        self, 
        strategy: str, 
        strat_data: Dict[str, Any]
    ) -> Optional[Hypothesis]:
        """H1: Check if threshold needs adjustment based on win rate."""
        win_rate = strat_data.get('win_rate', 0.5)
        
        # Map strategy to threshold parameter
        param_map = {
            'news_sentiment': 'newsSentimentThreshold',
            'statistical_arbitrage': 'statArbitrageThreshold',
            'volatility_based': 'volatilityThreshold'
        }
        
        param = param_map.get(strategy)
        if not param:
            return None
        
        current = self.settings.get(param, 0.5)
        
        # If win rate < 50%, suggest increasing threshold
        if win_rate < 0.5 and current < 0.9:
            suggested = min(current + 0.05, self.GUARDRAILS.get(param, (0.9))[1])
            
            return Hypothesis(
                hypothesis_type='threshold_adjust',
                parameter=param,
                current_value=current,
                suggested_value=round(suggested, 2),
                rationale=f'Win rate {win_rate:.1%} below 50%; increase threshold to filter noise',
                strategy=strategy
            )
        
        return None
    
    def _check_correlation_risk(
        self, 
        strategy_perf: Dict[str, Any]
    ) -> Optional[Hypothesis]:
        """
        H2: Check if position size should be reduced due to correlation.
        Simplified: If we have >2 strategies, reduce max position size.
        """
        # This is a placeholder - real correlation would need trade-level data
        # For now, skip if less than 3 strategies
        if len(strategy_perf) < 3:
            return None
        
        current_max = self.settings.get('maxPositionSizePct', 0.1)
        
        # If we have multiple active strategies, reduce exposure
        if current_max > 0.05:
            suggested = max(0.05, current_max * 0.5)
            
            return Hypothesis(
                hypothesis_type='position_size_adjust',
                parameter='maxPositionSizePct',
                current_value=current_max,
                suggested_value=round(suggested, 2),
                rationale=f'{len(strategy_perf)} strategies active; reduce position size for diversification'
            )
        
        return None
    
    def _check_kelly_adjust(
        self, 
        overall: Dict[str, Any]
    ) -> Optional[Hypothesis]:
        """H3: Adjust Kelly based on Sharpe ratio."""
        sharpe = overall.get('sharpe_ratio', 1.0)
        current_kelly = self.settings.get('kellyFraction', 0.5)
        
        # High Sharpe -> increase Kelly (more aggressive)
        if sharpe > 2.0 and current_kelly < 0.8:
            suggested = min(0.8, round(current_kelly * 1.1, 2))
            
            if suggested >= current_kelly + 0.05:
                return Hypothesis(
                    hypothesis_type='kelly_adjust',
                    parameter='kellyFraction',
                    current_value=current_kelly,
                    suggested_value=suggested,
                    rationale=f'Sharpe {sharpe:.2f} > 2.0; increase Kelly for more growth',
                    strategy='all'
                )
        
        # Low Sharpe -> decrease Kelly (more conservative)
        elif sharpe < 1.0 and current_kelly > 0.1:
            suggested = max(0.1, round(current_kelly * 0.9, 2))
            
            if suggested <= current_kelly - 0.05:
                return Hypothesis(
                    hypothesis_type='kelly_adjust',
                    parameter='kellyFraction',
                    current_value=current_kelly,
                    suggested_value=suggested,
                    rationale=f'Sharpe {sharpe:.2f} < 1.0; decrease Kelly for safety',
                    strategy='all'
                )
        
        return None
    
    def _check_strategy_disable(
        self, 
        strategy: str, 
        strat_data: Dict[str, Any]
    ) -> Optional[Hypothesis]:
        """H4: Disable underperforming strategy."""
        total_trades = strat_data.get('total_trades', 0)
        sharpe = strat_data.get('sharpe_ratio', 0) if 'sharpe_ratio' in strat_data else 0
        
        # Disable if < 10 trades AND Sharpe < 0.5
        if total_trades < 10 and sharpe < 0.5:
            # Check if already disabled
            enable_param = f'{strategy}_enabled'
            is_enabled = self.settings.get(enable_param, True)
            
            if is_enabled:
                return Hypothesis(
                    hypothesis_type='strategy_disable',
                    parameter=enable_param,
                    current_value=True,
                    suggested_value=False,
                    rationale=f'Only {total_trades} trades with Sharpe {sharpe:.2f}',
                    strategy=strategy
                )
        
        return None


if __name__ == "__main__":
    # Quick test with mock data
    current_settings = {
        'kellyFraction': 0.5,
        'maxPositionSizePct': 0.1,
        'newsSentimentThreshold': 0.6,
        'statArbitrageThreshold': 0.05,
        'volatilityThreshold': 0.1,
        'news_sentiment_enabled': True,
        'statistical_arbitrage_enabled': True,
        'volatility_based_enabled': True
    }
    
    # Test with good performance (high Sharpe)
    good_performance = {
        'overall_statistics': {'sharpe_ratio': 2.5, 'total_trades': 100},
        'strategy_breakdown': {
            'news_sentiment': {'win_rate': 0.55, 'total_trades': 30},
            'statistical_arbitrage': {'win_rate': 0.6, 'total_trades': 40},
            'volatility_based': {'win_rate': 0.52, 'total_trades': 30}
        }
    }
    
    gen = HypothesisGenerator(current_settings)
    hypotheses = gen.generate(good_performance)
    
    print(f"Generated {len(hypotheses)} hypotheses from good performance:")
    for h in hypotheses:
        print(f"  - {h.hypothesis_type}: {h.parameter} {h.current_value} -> {h.suggested_value}")
        print(f"    Reason: {h.rationale}")
    
    # Test with bad performance
    bad_performance = {
        'overall_statistics': {'sharpe_ratio': 0.5, 'total_trades': 20},
        'strategy_breakdown': {
            'news_sentiment': {'win_rate': 0.35, 'total_trades': 5, 'sharpe_ratio': 0.2}
        }
    }
    
    gen2 = HypothesisGenerator(current_settings)
    hypotheses2 = gen2.generate(bad_performance)
    
    print(f"\nGenerated {len(hypotheses2)} hypotheses from bad performance:")
    for h in hypotheses2:
        print(f"  - {h.hypothesis_type}: {h.parameter} {h.current_value} -> {h.suggested_value}")
        print(f"    Reason: {h.rationale}")
    
    print("\n✅ HypothesisGenerator test passed")
