# Agent Session Memory & Strategy Status

## Current Context (October 15, 2026)
- **Account Capital**: $968,998.88 USDC (down from $1,000,000.00 initial capital, -$31,001.12 total drawdown).
- **Recent Net Performance (Oct 12 - 15)**: -$26,570.43 net realized loss across 298 fills.
- **Exchange Fees Paid**: $10,105.11.

## Root Cause Diagnostics
1. **Inverted Risk-Reward (1 : 5.7 Negative Expectancy)**:
   - Line 1224 in `examples/quantitative_trading_agent.py` cuts winners at `pnl_pct >= 0.0035` (+35 bps) or $2,000.
   - Lines 1207-1210 allow stop losses to run to `-1.80%` (Module A) and `-2.00%` (Module C).
   - Requires >91% win rate to break even after exchange fees. Realized win rate was ~49%, resulting in continuous bleed.
2. **Oversized Notionals on 1-Minute Candle Noise**:
   - `MAX_SINGLE_TRADE_NOTIONAL` set to $250,000; ETH target $200k, BTC $250k, SOL $150k.
   - Normal 1.5% 1m intraday noise created repetitive -$2,000 to -$3,000 losses.
3. **Severe ETH Directional Losses**:
   - ETH-USDC-PERP accounted for -$18,564.34 of the net loss (48 losing closes vs 20 wins).
4. **Aggressive Taker Stops & Fee Bleed**:
   - Stop-loss exits use IOC taker execution, costing $110-$220 per exit in fees and spread.

## Next Steps Planned
1. Apply Phase 1 Risk Containment: Reduce `MAX_SINGLE_TRADE_NOTIONAL` to $80,000 and `MAX_PORTFOLIO_NOTIONAL` to $180,000.
2. Eliminate premature +35 bps profit harvest cap and calibrate a minimum 1 : 1.5 positive risk/reward ratio.
3. Add higher-timeframe trend confirmation filter (5m/15m) to filter out 1m false breakouts.
