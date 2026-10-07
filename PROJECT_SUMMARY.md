# GoDark Quant Agent - Complete Project Summary

## Project Overview
**Autonomous quantitative trading agent** for GoDark DEX perpetual markets (BTC-USDC-PERP, ETH-USDC-PERP, SOL-USDC-PERP) running on GitHub Actions free tier.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    QUANT AGENT (Single Process)             │
├─────────────────────────────────────────────────────────────┤
│  Regime Detector  →  Risk Allocator  →  Execution Engine   │
│  (ADX, BB, Funding, OI)    (1.5%/trade)      (WS Client)  │
├─────────────────────────────────────────────────────────────┤
│  ACTIVE MODULE (one per symbol at a time)                  │
│  ┌─────────┐ ┌─────────┐ ┌────────────────┐ ┌────────────┐ │
│  │Module A │ │Module B │ │Module C        │ │Module D    │ │
│  │Trend    │ │Grid/MR  │ │Funding Momentum│ │Breakout    │ │
│  │EMA×RSI  │ │BB/ADX   │ │±0.05% funding  │ │Keltner+OI  │ │
│  └─────────┘ └─────────┘ └────────────────┘ └────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

---

## Module Specifications

| Module | Strategy | Weight | Leverage | Max Pos | Trigger |
|--------|----------|--------|----------|---------|---------|
| **A - Trend** | EMA20×EMA50 cross + RSI 50-65/35-50 | 45% | 3x | 2 | ADX > 25 |
| **B - Grid/MR** | BB mean-reversion + ADX < 20 | 25% | 2x | 3 | ADX < 20 or price in BB |
| **C - Funding** | Funding ≤ -0.05% (LONG) / ≥ +0.05% (SHORT) | 15% | 2x | 2 | \|Funding\| > 0.05% |
| **D - Breakout** | Keltner breakout + OI surge >20% + vol > 1.5x | 15% | 3x | 1 | Keltner break + OI surge |

---

## Current Architecture Flow

```
evaluate_symbol() → _detect_regime() → Route to Module A/B/C/D
     ↓
place_directional_order() / execute_grid_module()
     ↓
TradeLogger.log_trade() / log_grid() + TelegramNotifier
```

---

## Files Structure

```
gdx-python-sdk-examples/
├── .github/workflows/quant-agent.yml    # GitHub Actions workflow (runs every 6h)
├── requirements.txt                      # Python dependencies
├── .gitignore
├── README.md
└── examples/
    ├── quantitative_trading_agent.py    # Main agent (1048 lines)
    ├── dotenv.py                         # Env loader
    └── trade_safety.py                   # Safety utilities
```

---

## Key Fixes Applied (Chronological)

| # | Issue | Fix |
|---|-------|-----|
| 1 | WebSocket reconnects every 30s | TransportConfig: heartbeat=20s, stale_timeout=900s, missed_limit=10 |
| 2 | Slow warmup (100s) | FEED_INTERVAL_SECONDS=2 (was 5) |
| 3 | MIN_NOTIONAL errors (2013) | MIN_NOTIONAL_USD=100 (was 25) |
| 4 | Book confirmation timeout | confirmation="ack" for grid orders |
| 5 | WS idle disconnect | Subscribe to funding_rate, open_interest, volume |
| 6 | Circular import: types.py | Renamed sdk/godark/types.py → data_types.py |
| 7 | Missing pyhpke/protobuf | Added to requirements.txt |
| 8 | SDK install issues | Install from upstream: `git+https://github.com/gq-godark/gdx-python-sdk-examples.git#subdirectory=sdk` |
| 9 | Missing `deque` import | Added `from collections import deque` |
| 10 | Indentation errors in grid module | Fixed try/except indentation |
| 11 | **REMOVED DRY_RUN** | Removed all 7 DRY_RUN checks, removed env var |
| 12 | **Added TradeLogger** | JSONL format to trades.log |
| 13 | **Added TelegramNotifier** | Real-time alerts for trades, grids, regime switches |
| 14 | **Complete Telegram hooks** | Trade, grid, regime switch notifications |
| 15 | **TradeLogger class** | JSONL format trades.log + artifact upload |
| 16 | **Artifact upload** | trades.log uploaded as 90-day artifact |

---

## Current File Status

### `examples/quantitative_trading_agent.py` (1048 lines)
- ✅ Imports fixed (deque, aiohttp, json, datetime)
- ✅ TradeLogger class added (JSONL format)
- ✅ TelegramNotifier class added (HTML formatted)
- ✅ TradeLogger integrated in agent
- ✅ TelegramNotifier integrated in agent
- ✅ DRY_RUN completely removed (7 locations)
- ✅ Trade logging in `place_directional_order()`
- ✅ Grid logging in `execute_grid_module()`
- ✅ Regime switch logging in `_switch_module()`
- ✅ Telegram hooks for trades, grids, regime switches
- ✅ Indentation fixed in grid module

### `.github/workflows/quant-agent.yml`
- ✅ Installs SDK from upstream: `git+https://github.com/gq-godark/gdx-python-sdk-examples.git#subdirectory=sdk`
- ✅ Requirements: pandas, numpy, httpx, pydantic, websockets, pyyaml, python-dotenv, protobuf>=7.35.1, pyhpke, aiohttp
- ✅ Upload trades.log as 90-day artifact
- ✅ Uses upstream SDK (no local copy)
- ⚠️ Still has `DRY_RUN: "false"` in env (needs removal)

### `requirements.txt`
```
pandas
numpy
httpx
pydantic
websockets
pyyaml
python-dotenv
protobuf>=7.35.1
pyhpke
aiohttp
```

---

## Current Issues

### ✅ Fixed (Deployed)
- WebSocket stability (20s heartbeat, 15min stale timeout)
- All imports working
- DRY_RUN removed from agent code
- Trade logging implemented
- Telegram notifications ready
- Artifact upload configured
- Upstream SDK installation

### ⚠️ Pending / Needs Verification

| Issue | Status | Priority |
|-------|--------|----------|
| Remove `DRY_RUN: "false"` from workflow.yml | **PENDING** | High |
| Verify Telegram secrets set in GitHub | **PENDING** | High |
| Verify agent actually places trades (not just warmup) | **UNVERIFIED** | Critical |
| Verify price data quality (ccy/oi from REST) | **UNVERIFIED** | High |
| Verify regime detection triggers correctly | **UNVERIFIED** | High |
| Verify trade execution on testnet | **UNVERIFIED** | Critical |
| Verify Telegram alerts fire | **UNVERIFIED** | High |
| Verify trade.log artifact uploads | **UNVERIFIED** | Medium |

---

## GitHub Secrets Required (Already Set)

| Secret | Value |
|--------|-------|
| `GODARK_API_KEY_ID` | `gdk_4e4e691ce1b5ab1ee1ccdaa7f3b09f88` |
| `GODARK_API_SECRET` | `4e9425b7920c4e11a161ad8e9af5330b31f160fd91d1c72ba9c1c12fa68d8375` |
| `GODARK_PASSPHRASE` | `asdfghjkl` |
| `TELEGRAM_BOT_TOKEN` | (from @BotFather) |
| `TELEGRAM_CHAT_ID` | (from @userinfobot) |

---

## Deployment Details

### GitHub Actions Schedule
- **Cron**: `0 */6 * * *` (00:00, 06:00, 12:00, 18:00 UTC)
- **Duration**: 350 min max (5h 50min)
- **Runs per 18 days**: 72 runs (4/day × 18)
- **Free tier**: Unlimited minutes on public repo

### Run Flow
```
1. Checkout → Setup Python 3.11 → Cache venv
2. Install deps + upstream SDK
3. Run agent (340 min timeout)
4. Upload trades.log artifact (90-day retention)
```

### Testnet Details
- **Endpoint**: `wss://api.godark-dex.com/ws/v1` / `https://api.godark-dex.com`
- **Symbols**: BTC-USDC-PERP, ETH-USDC-PERP, SOL-USDC-PERP
- **Testnet USDC**: Free from faucet at https://app.godark-dex.com
- **No real money at risk**

---

## Known Gaps / To-Do

### Immediate (Before Next Run)
1. **Remove DRY_RUN from workflow.yml** - Line 37 in quant-agent.yml
2. **Verify Telegram secrets** are set in GitHub repo settings
3. **Trigger manual run** to test new code with Telegram

### Post-Deployment Verification
1. **Check trade execution** - Verify orders placed on testnet
2. **Verify Telegram alerts** - Check for trade/grid/regime messages
3. **Check trades.log artifact** - Verify JSONL format
4. **Monitor regime detection** - Ensure all 4 modules trigger
5. **Monitor warmup time** - Should be ~40s (20 candles × 2s)

### Potential Issues to Watch
- **Price data quality**: REST API `ccy/oi` may be stale
- **Regime detection**: May not trigger if thresholds too tight
- **Warmup time**: 40s minimum before first trade
- **Testnet liquidity**: May have wide spreads
- **Rate limits**: REST polling every 2s + WS

---

## Run Commands

### Local Test
```bash
cd gdx-python-sdk-examples/examples
../.venv/bin/python quantitative_trading_agent.py
```

### Trigger Manual Run
```bash
gh workflow run "GoDark Quant Agent (Bootcamp)" --repo barshal-horse/Go-qunat-bootcamp
```

### View Logs
```bash
gh run view --repo barshal-horse/Go-qunat-bootcamp
# Or visit: https://github.com/barshal-horse/Go-qunat-bootcamp/actions
```

---

## Quick Reference: Key Code Locations

| Feature | File | Line Range |
|---------|------|------------|
| Main entry | `quantitative_trading_agent.py` | `main()` line 1000+ |
| Trading loop | `run_trading_loop()` | Line 965 |
| Symbol evaluation | `evaluate_symbol()` | Line 849 |
| Regime detection | `_detect_regime()` | Line 553 |
| Module A (Trend) | `execute_trend_module()` | Line ~640 |
| Module B (Grid) | `execute_grid_module()` | Line ~485 |
| Module C (Funding) | `execute_funding_module()` | Line ~629 |
| Module D (Breakout) | `execute_breakout_module()` | Line ~592 |
| Order placement | `place_directional_order()` | Line ~416 |
| Grid placement | `execute_grid_module()` | Line ~485 |
| Trade logging | `TradeLogger` class | Line ~95 |
| Telegram notifier | `TelegramNotifier` class | Line ~23 |

---

## Next Action Items

### Immediate (Do Now)
1. **Remove DRY_RUN from workflow.yml** (line 37)
2. **Push to GitHub**
3. **Cancel current run #10** and **re-run workflow** to test new code

### After Next Run
1. Check GitHub Actions logs for trade execution
2. Check Telegram for notifications
3. Download `trades.log` artifact
4. Verify on GoDark dashboard

---

## Quick Reference: Key Code Locations

| Feature | File | Line Range |
|---------|------|------------|
| Main entry | `quantitative_trading_agent.py` | `main()` line 1000+ |
| Trading loop | `run_trading_loop()` | Line 965 |
| Symbol evaluation | `evaluate_symbol()` | Line 849 |
| Regime detection | `_detect_regime()` | Line 553 |
| Module A (Trend) | `execute_trend_module()` | Line ~640 |
| Module B (Grid) | `execute_grid_module()` | Line ~485 |
| Module C (Funding) | `execute_funding_module()` | Line ~629 |
| Module D (Breakout) | `execute_breakout_module()` | Line ~592 |
| Order placement | `place_directional_order()` | Line ~416 |
| Grid placement | `execute_grid_module()` | Line ~485 |
| Trade logging | `TradeLogger` class | Line ~95 |
| Telegram notifier | `TelegramNotifier` class | Line ~23 |

---

## Next Action Items

### Immediate (Do Now)
1. **Remove DRY_RUN from workflow.yml** (line 37)
2. **Push to GitHub**
3. **Cancel current run #10** and **re-run workflow** to test new code

### After Next Run
1. Check GitHub Actions logs for trade execution
2. Check Telegram for notifications
3. Download `trades.log` artifact
4. Verify on GoDark dashboard

---

This document should give any model/person full context to continue development or debugging.
EOF
echo "Saved to PROJECT_SUMMARY.md"