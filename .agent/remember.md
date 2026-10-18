# Agent Session Memory & Strategy Status

## Current Context (October 18, 2026 - 17:55 IST / 12:25 UTC)
- **Time**: 17:55 IST Oct 18 (12:25 UTC Oct 18).
- **Deadline**: October 26, 2026 (~8 days remaining).
- **Total Account Collateral**: **$962,262.36 USDC** (96.2% of capital preserved).
- **Free Collateral**: **$928,910.58 USDC** (96.5% in cash buffer; maximum safety).
- **Strategy State**: Upgraded from v5.0.0 conservative sniper to **v6.1.0 Active 2-Tier Alpha Sniper Engine**.

## Architectural Upgrades: Engine v6.1.0 Active 2-Tier Alpha Sniper
1. **Asymmetric 2-Tier Sizing Architecture**:
   - **Tier 1 (High-Conviction Alpha Expansion)**: Confidence $\ge 78$, 1H Macro Alignment, $ADX \ge 25.0$. Sized at **$250k BTC / $220k ETH / $180k SOL** to capture +$9,000 to +$11,500 runners.
   - **Tier 2 (Tactical Momentum Runner)**: Confidence $65 \le C < 78$, $ADX \ge 22.0$. Sized at **$150k BTC / $130k ETH / $110k SOL** to bank +$3,600 to +$4,500 wins.
   - Guaranteed active trading cadence (1–3 trades/day) without sitting idle.
2. **Carry Position Dynamic Upgrader**:
   - Small $50k Module C carry trades are automatically closed and upgraded to full Module A Trend Runners whenever a valid directional trend breakout occurs on the same symbol.
3. **Module C Regime Subordination**:
   - `_detect_regime` enforces that funding carry is only active in non-trending conditions ($ADX < 25.0$). In strong trend expansions ($ADX \ge 25.0$), Module A directional trend trading takes unconditional precedence.
4. **Tight Downside Risk Shield**:
   - Fixed -0.85% Hard Stop Loss with 120-minute anti-chop quarantine.
   - Breakeven lock active at +0.85% peak gain to protect accumulated profits.

## Verification
- **Test Suite**: 16/16 unit and simulation tests passing on disk (`tests/test_agent_simulation.py`).
- **Risk Caps**: Single trade cap $250k, total portfolio cap $500k (leaving >$450k cash buffer at all times).

## Next Steps
- Push changes to `origin/main` to trigger the active runner refresh on GitHub Actions.
- Monitor execution of incoming 15m breakout candles across BTC, ETH, and SOL.
