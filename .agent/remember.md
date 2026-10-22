# Agent Session Memory & Strategy Status

## Current Context (October 22, 2026 - 16:20 IST / 10:50 UTC)
- **Time**: 16:20 IST Oct 22 (10:50 UTC Oct 22).
- **Deadline**: October 26, 2026 (~4 days remaining).
- **Total Account Collateral**: **$948,975.07 USDC**.
- **Free Collateral**: **$862,300.45 USDC** (90.9% liquid cash).
- **Target Recovery to Breakeven**: **+$51,024.93**.
- **Strategy State**: Deployed **Engine v7.0.0 Tournament Alpha Blitz Mode**.

## Architecture & Parameters: Engine v7.0.0 Tournament Blitz Mode
1. **Asymmetric 2x High-Alpha Sizing**:
   - **Tier 1 (High Conviction $\ge 78$, 1H Macro Alignment)**:
     - **BTC-USDC-PERP**: **$500,000.00**
     - **ETH-USDC-PERP**: **$450,000.00**
     - **SOL-USDC-PERP**: **$350,000.00**
     - Single +5.5% runner capture on Tier 1 yields **+$27,500**! Just 2 winning runners completely eliminates the -$51,025 deficit.
   - **Tier 2 (Tactical Momentum Runner $65 \le C < 78$)**:
     - **BTC-USDC-PERP**: **$300,000.00**
     - **ETH-USDC-PERP**: **$250,000.00**
     - **SOL-USDC-PERP**: **$200,000.00**
     - Captures **+$16,500** per runner.
2. **Breathing Room Stop Loss & Asymmetric R:R**:
   - **Stop Loss**: **-1.40%** (`stop_loss_pct: 0.0140`). Gives trades ample breathing room against normal 15m ATR noise, avoiding false-wick premature stopouts.
   - **Take Profit Target**: **+5.50%** (`target_tp_pct: 0.055`).
   - **Reward-to-Risk Ratio**: **3.93 : 1** (Risking $7k to make $27.5k).
   - **Breakeven Shield Lock**: **+1.20%** (`breakeven_trigger_pct: 0.0120`). Once position enters momentum expansion (+1.2%), entry price is shielded risk-free.
3. **Risk Management Caps**:
   - Single trade cap: **$500,000**
   - Total portfolio ceiling: **$1,000,000** (~1.05x leverage — eliminates exchange liquidation risk while giving full firepower).

## Verification
- **Test Suite**: 16/16 unit and simulation tests passed cleanly (`tests/test_agent_simulation.py`).
- **Disk Verification**: Verified on disk against Python 3.10.

## Next Steps
- Commit and push to `origin/main` to trigger active runner reload on GitHub Actions.
- Monitor execution of incoming 15m breakout candles across BTC, ETH, and SOL.
