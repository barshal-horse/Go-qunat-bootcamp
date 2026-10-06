# GoDark Quant Agent (Bootcamp)

Autonomous quantitative trading agent for GoDark DEX perpetual markets (BTC-USDC-PERP, ETH-USDC-PERP, SOL-USDC-PERP).

## Features

- **Multi-Strategy Architecture** with regime-based routing
- **Module A - Trend Following**: EMA20×EMA50 crossover + RSI filter (45% weight, 3x leverage)
- **Module B - Grid/Mean Reversion**: Bollinger Bands + ADX < 20 (25% weight, 2x leverage)
- **Module C - Funding Momentum**: Trade extreme funding rates ±0.05% (15% weight, 2x leverage)
- **Module D - OI Breakout**: Keltner Channel breakout + Open Interest surge (15% weight, 3x leverage)

## Risk Management

- 1.5% portfolio risk per trade (Module A, B, D) / 1% (Module C)
- Portfolio heat limit: 80% max collateral utilization
- Max positions per module: Trend=2, Grid=3, Funding=2, Breakout=1
- Mutual exclusion: one active module per symbol

## Deployment

Runs on GitHub Actions (free public repo):
- **Schedule**: Every 6 hours (00:00, 06:00, 12:00, 18:00 UTC)
- **Runtime**: Up to 5h50m per run
- **Duration**: 18 days (72 runs)

## Setup

1. Add secrets to GitHub repo (Settings → Secrets → Actions):
   - `GODARK_API_KEY_ID`
   - `GODARK_API_SECRET`
   - `GODARK_PASSPHRASE`

2. Trigger workflow: Actions → GoDark Quant Agent (Bootcamp) → Run workflow

## Local Development

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python examples/quantitative_trading_agent.py
```

## Architecture

- WebSocket (godark SDK) for order/position streams + public market data
- REST for price feed (open interest implied mark) + account management
- 20s heartbeat / 15min stale timeout for stable connections
- Regime detector routes to appropriate module per symbol