# Agent Session Memory & Strategy Status

## Current Context (October 15, 2026 - Bootcamp Final Stretch)
- **Deadline**: October 26, 2026 (~11 days remaining).
- **Core Mission**: Recover -$31,001.12 drawdown and maximize total PnL with minimum trade turnover.
- **Account Capital**: $969,028.41 USDC (Initial: $1,000,000.00).

## 15m Sniper Maximum Profit Engine (v4.0.0-sniper)
1. **Timeframe Shift**: Switched from 1m tick noise to authentic 15-minute candles (`interval: "15m"`) via Hyperliquid reference feeds (3 days / 288 periods lookback).
2. **Sniper Position Sizing**:
   - BTC-USDC-PERP: $180,000
   - ETH-USDC-PERP: $150,000
   - SOL-USDC-PERP: $120,000
   - Max portfolio notional: $360,000 (~0.36x safe leverage).
3. **Asymmetric Risk/Reward & Armor**:
   - Hard stop loss: -0.90% ($1,350 - $1,620 max risk per trade).
   - Auto-Breakeven Shield: The moment price hits +1.00%, stop loss jumps to Breakeven (+0.05% fee buffer). Zero risk of winning trades turning into losses.
   - Trailing Runner: Engages at +2.20% peak, trailing by 80 bps.
   - Major Target: +4.80% ($7,000 - $8,600 profit per runner).
4. **Strict Anti-Churn & Discipline**:
   - ADX >= 22 required for trend confirmation.
   - Trade cooldown: 1800s (30 minutes).
   - Total trade budget: ~10 to 18 high-conviction trades across the entire 11-day period.

## Verification & Deployment
- All 13 simulation unit tests in `tests/test_agent_simulation.py` passed cleanly (including breakeven shield test).
