#!/usr/bin/env python3
"""
GoDark Autonomous Quantitative Trading Agent
Target Markets: BTC-USDC-PERP, ETH-USDC-PERP, SOL-USDC-PERP
Protocol: WebSocket (godark SDK) + REST Base Client
"""

import asyncio
import logging
import math
import os
import sys
import signal
import time
import json
from collections import deque
from decimal import Decimal, ROUND_DOWN, ROUND_UP, ROUND_HALF_UP
from typing import Dict, List, Optional, Tuple, Set, Deque, Any
from datetime import datetime

import httpx
import pandas as pd
import numpy as np

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# -----------------------------------------------------------------------------
# Telegram Notifier
# -----------------------------------------------------------------------------
class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token.strip() if bot_token else ""
        self.chat_id = chat_id.strip() if chat_id else ""
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
        self.enabled = bool(self.bot_token and self.chat_id)

    async def send(self, text: str) -> bool:
        if not self.enabled:
            return False
        try:
            import aiohttp
            timeout = aiohttp.ClientTimeout(total=8.0)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{self.base_url}/sendMessage",
                    json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"},
                ) as resp:
                    return resp.status == 200
        except Exception as e:
            logging.warning(f"Telegram notification failed: {e}")
            return False

    def format_trade(
        self,
        symbol: str,
        side: str,
        price: float,
        qty: float,
        module: str,
        order_id: Optional[str] = None,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
    ) -> str:
        emoji = "🟢" if side == "BUY" else "🔴"
        mod_name = {"A": "Trend", "B": "Grid", "C": "Funding", "D": "Breakout"}.get(module, module)
        lines = [
            f"{emoji} <b>Trade Executed</b> {emoji}",
            f"<b>Symbol:</b> {symbol}",
            f"<b>Side:</b> {side}",
            f"<b>Price:</b> {price:,.4f}",
            f"<b>Qty:</b> {qty}",
            f"<b>Module:</b> {mod_name} ({module})",
        ]
        if sl:
            lines.append(f"<b>SL:</b> {sl:,.4f}")
        if tp:
            lines.append(f"<b>TP:</b> {tp:,.4f}")
        if order_id:
            lines.append(f"<b>Order ID:</b> <code>{order_id}</code>")
        lines.append(f"<b>Time:</b> {datetime.utcnow().strftime('%H:%M:%S UTC')}")
        return "\n".join(lines)

    def format_grid(
        self,
        symbol: str,
        bid_price: float,
        ask_price: float,
        qty: float,
        order_ids: list,
    ) -> str:
        lines = [
            f"📊 <b>Grid Placed</b> 📊",
            f"<b>Symbol:</b> {symbol}",
            f"<b>Bid:</b> {bid_price:,.4f} | <b>Ask:</b> {ask_price:,.4f}",
            f"<b>Qty per leg:</b> {qty}",
            f"<b>Orders:</b> {len(order_ids)} placed",
            f"<b>Time:</b> {datetime.utcnow().strftime('%H:%M:%S UTC')}",
        ]
        return "\n".join(lines)

    def format_regime_switch(
        self, symbol: str, old_module: str, new_module: str, regime: str
    ) -> str:
        mod_name = {"A": "Trend", "B": "Grid", "C": "Funding", "D": "Breakout"}
        old_name = mod_name.get(old_module, old_module)
        new_name = mod_name.get(new_module, new_module)
        return (
            f"🔄 <b>Regime Switch</b>\n"
            f"<b>Symbol:</b> {symbol}\n"
            f"<b>Regime:</b> {regime}\n"
            f"<b>Module:</b> {old_name} -> {new_name}"
        )

    def format_fill(
        self,
        symbol: str,
        side: str,
        price: float,
        qty: float,
        realized_pnl: Optional[float] = None,
        fee: Optional[float] = None,
        order_id: Optional[str] = None,
    ) -> str:
        emoji = "🎯"
        lines = [
            f"{emoji} <b>Order Filled</b> {emoji}",
            f"<b>Symbol:</b> {symbol}",
            f"<b>Side:</b> {side}",
            f"<b>Fill Price:</b> {price:,.4f}",
            f"<b>Fill Qty:</b> {qty}",
        ]
        if realized_pnl is not None:
            pnl_emoji = "🟩" if realized_pnl >= 0 else "🟥"
            lines.append(f"<b>Realized PnL:</b> {pnl_emoji} ${realized_pnl:,.4f}")
        if fee is not None:
            lines.append(f"<b>Est. Fee:</b> ${fee:,.4f}")
        if order_id:
            lines.append(f"<b>Order ID:</b> <code>{order_id}</code>")
        lines.append(f"<b>Time:</b> {datetime.utcnow().strftime('%H:%M:%S UTC')}")
        return "\n".join(lines)

    def format_close(
        self,
        symbol: str,
        side: str,
        price: float,
        qty: float,
        reason: str,
        unrealized_pnl: Optional[float] = None,
    ) -> str:
        emoji = "🏁"
        pnl_str = ""
        if unrealized_pnl is not None:
            pnl_emoji = "🟩" if unrealized_pnl >= 0 else "🟥"
            pnl_str = f"\n<b>Est. PnL:</b> {pnl_emoji} ${unrealized_pnl:,.2f}"
        return (
            f"{emoji} <b>Position Closed</b> {emoji}\n"
            f"<b>Symbol:</b> {symbol}\n"
            f"<b>Action:</b> {side} {qty}\n"
            f"<b>Exit Price:</b> {price:,.4f}\n"
            f"<b>Reason:</b> {reason}"
            f"{pnl_str}\n"
            f"<b>Time:</b> {datetime.utcnow().strftime('%H:%M:%S UTC')}"
        )


# -----------------------------------------------------------------------------
# Trade Logger (JSONL)
# -----------------------------------------------------------------------------
class TradeLogger:
    def __init__(self, log_file: str = "trades.log"):
        self.log_file = log_file
        # Ensure file exists immediately so artifacts never fail to upload
        if not os.path.exists(self.log_file):
            try:
                with open(self.log_file, "w", encoding="utf-8") as f:
                    f.write(json.dumps({"type": "init", "timestamp": datetime.utcnow().isoformat() + "Z"}) + "\n")
            except Exception as e:
                logging.warning(f"Failed initializing {self.log_file}: {e}")

    def _write(self, record: dict):
        record["timestamp"] = datetime.utcnow().isoformat() + "Z"
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            logging.warning(f"Failed to append to trade log {self.log_file}: {e}")

    def log_trade(
        self,
        symbol: str,
        side: str,
        price: float,
        qty: float,
        module: str,
        order_id: Optional[str] = None,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
        regime: Optional[str] = None,
    ):
        self._write({
            "type": "trade",
            "symbol": symbol,
            "side": side,
            "price": price,
            "qty": qty,
            "module": module,
            "order_id": order_id,
            "sl": sl,
            "tp": tp,
            "regime": regime,
        })

    def log_grid(
        self,
        symbol: str,
        bid_price: float,
        ask_price: float,
        qty: float,
        order_ids: list,
        module: str = "B",
    ):
        self._write({
            "type": "grid",
            "symbol": symbol,
            "bid_price": bid_price,
            "ask_price": ask_price,
            "qty_per_leg": qty,
            "order_ids": order_ids,
            "module": module,
        })

    def log_regime_switch(self, symbol: str, old_module: str, new_module: str, regime: str):
        self._write({
            "type": "regime_switch",
            "symbol": symbol,
            "old_module": old_module,
            "new_module": new_module,
            "regime": regime,
        })

    def log_fill(
        self,
        symbol: str,
        side: str,
        fill_price: float,
        fill_qty: float,
        realized_pnl: Optional[float] = None,
        trading_fee: Optional[float] = None,
        order_id: Optional[str] = None,
    ):
        self._write({
            "type": "fill",
            "symbol": symbol,
            "side": side,
            "fill_price": fill_price,
            "fill_qty": fill_qty,
            "realized_pnl": realized_pnl,
            "trading_fee": trading_fee,
            "order_id": order_id,
        })


# -----------------------------------------------------------------------------
# Configuration & Setup
# -----------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("GoDarkQuantAgent")

try:
    from godark import (
        GodarkClient,
        MarketDataClient,
        GodarkRestClient,
        PlaceOrderOptions,
        TransportConfig,
        Side,
        OrderType,
        TimeInForce,
    )
    from godark._symbols import load_offline_decimals_map
    from godark.errors import (
        GodarkError,
        AuthenticationError,
        SessionError,
        OrderError,
        ConnectionError as GDXConnectionError,
        TimeoutError as GDXTimeoutError,
    )
except ImportError:
    logger.critical(
        "Could not import 'godark'. Ensure script is executed in an environment with "
        "the godark package installed."
    )
    sys.exit(1)

# Telegram Notifier Instance
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
telegram_notifier = TelegramNotifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
if telegram_notifier.enabled:
    logger.info("Telegram notifications ENABLED.")
else:
    logger.info("Telegram notifications DISABLED (credentials missing in environment).")

# Configure WebSocket transport
transport_config = TransportConfig(
    heartbeat_interval=20.0,
    stale_timeout=900.0,
    missed_heartbeat_limit=10,
    command_timeout=60.0,
)

API_KEY_ID = os.getenv("GODARK_API_KEY_ID")
API_SECRET = os.getenv("GODARK_API_SECRET")
PASSPHRASE = os.getenv("GODARK_PASSPHRASE")
WS_URL = os.getenv("GODARK_WS_URL", "wss://api.godark-dex.com")
DRY_RUN = os.getenv("DRY_RUN", "false").lower() in ("true", "1", "yes")

if not all([API_KEY_ID, API_SECRET, PASSPHRASE]):
    logger.critical("Missing required environment variables: GODARK_API_KEY_ID, GODARK_API_SECRET, or GODARK_PASSPHRASE")
    sys.exit(1)

REST_BASE_URL = WS_URL.replace("wss://", "https://").replace("ws://", "http://")
SYMBOLS = ["BTC-USDC-PERP", "ETH-USDC-PERP", "SOL-USDC-PERP"]
DEFAULT_LEVERAGE = 3
# Risk & Capital Architecture (Optimized for $1M pool targeting 30% APR / ~$822/day)
# 0.20% risk per trade = $2,000 risk budget. At 1% SL = $100k-$120k notional position.
RISK_FACTOR = 0.002
MAX_SINGLE_TRADE_NOTIONAL = 120000.0  # Max $120k notional per single trade (~0.12x portfolio leverage)
MAX_PORTFOLIO_NOTIONAL = 400000.0     # Max $400k total notional across all 3 assets (~0.40x total leverage)
TRADE_COOLDOWN_SECONDS = 600          # 10-minute cooldown per symbol to eliminate overtrading churn

LOOP_INTERVAL_SECONDS = 15
FEED_INTERVAL_SECONDS = 2
MIN_NOTIONAL_USD = 100.0
DECIMALS_MAP = load_offline_decimals_map()
SYMBOL_IDS = {"BTC-USDC-PERP": 1, "ETH-USDC-PERP": 2, "SOL-USDC-PERP": 5}
SYMBOL_BY_ID = {v: k for k, v in SYMBOL_IDS.items()}
SYNC_THROTTLE_SECONDS = 5.0
# Live mid-price reference (venue exposes no price feed; Hyperliquid is the
# reference venue GoDark's own UI displays). Keys are Hyperliquid coin names.
REFERENCE_MID_URL = "https://api.hyperliquid.xyz/info"
REFERENCE_MID_KEYS = {"BTC-USDC-PERP": "BTC", "ETH-USDC-PERP": "ETH", "SOL-USDC-PERP": "SOL"}

# Strategy Parameters & Fee Economics
# Core Tier Default: Maker = 0.015% (1.5 bps), Taker = 0.055% (5.5 bps)
DEFAULT_MAKER_FEE_PCT = 0.00015
DEFAULT_TAKER_FEE_PCT = 0.00055
MAKER_OFFSET_BPS = 0.0003  # 3 bps passive limit offset from mid for maker capture
MIN_ATR_RATIO = 0.006      # 0.6% price floor for ATR to prevent micro-stops on 2s ticks
MAX_FEE_TO_PROFIT_RATIO = 0.25  # Block trade if fees+funding exceed 25% of expected TP profit
REGIME_STABILITY_CYCLES = 2    # Consecutive cycles needed before regime transition

# Module C: Funding Carry Arbitrage (Primary Structural Yield Engine)
FUNDING_ENTRY_THRESHOLD = 0.00010   # 10 bps/hr (~87.6% APR) threshold to enter carry trade
FUNDING_LONG_THRESHOLD = -0.00010   # Shorts pay longs: enter LONG
FUNDING_SHORT_THRESHOLD = 0.00010   # Longs pay shorts: enter SHORT
FUNDING_EXIT_THRESHOLD = 0.00003    # 3 bps/hr threshold to exit when carry normalizes
FUNDING_MAX_HOLD_HOURS = 24
TARGET_CARRY_NOTIONAL = 70000.0     # Target $70k notional per carry asset ($140k total on 2 assets = 0.14x leverage)

# Module E: Delta-Neutral Cointegrated Stat-Arb (ETH/BTC Pair Engine)
STAT_ARB_ENTRY_Z = 1.75             # Enter pair when ratio diverged >= 1.75 standard deviations
STAT_ARB_EXIT_Z = 0.30              # Exit pair when ratio mean-reverts to within 0.30 std devs
STAT_ARB_STOP_Z = 3.20              # Divergence stop-loss threshold
STAT_ARB_NOTIONAL = 30000.0         # $30k per leg ($60k total pair notional = 0.06x leverage)
STAT_ARB_LOOKBACK = 60              # 60 rolling ticks/periods for z-score calculation

# Module B: Dense Maker Market Making with Instant Round-Turn
MM_ROUND_TURN_SPREAD_BPS = 0.0020   # 20 bps target for instant market-making round-turn

OI_LOOKBACK_HOURS = 4
OI_SURGE_THRESHOLD = 0.20

KELTNER_PERIOD = 20
KELTNER_ATR_MULT = 2.0

REGIME_CONFIG = {
    "FUNDING":     {"module": "C", "weight": 0.50, "leverage": 2, "max_pos": 2},
    "STAT_ARB":    {"module": "E", "weight": 0.30, "leverage": 2, "max_pos": 2},
    "MEAN_REV":    {"module": "B", "weight": 0.20, "leverage": 2, "max_pos": 3},
    "TREND":       {"module": "A", "weight": 0.00, "leverage": 2, "max_pos": 0},
    "BREAKOUT":    {"module": "D", "weight": 0.00, "leverage": 2, "max_pos": 0},
}
MAX_PORTFOLIO_HEAT = 0.80

# Calibrated parameters & RL dynamic quoting defaults (Dense 3-tier inside spreads)
GRID_OFFSETS_BPS = {
    "SOL-USDC-PERP": [0.0016, 0.0032, 0.0055],  # 16 bps, 32 bps, 55 bps
    "ETH-USDC-PERP": [0.0018, 0.0036, 0.0060],  # 18 bps, 36 bps, 60 bps
    "BTC-USDC-PERP": [0.0020, 0.0040, 0.0070],  # 20 bps, 40 bps, 70 bps
}
INVENTORY_SKEW_GAMMA = 0.0001
BREAKEVEN_ATR_MULT = 0.75
TRAILING_TRIGGER_ATR_MULT = 1.25
TRAILING_RETRACE_RATIO = 0.35
TAKE_PROFIT_ATR_MULT = 2.2
STOP_LOSS_ATR_MULT = 1.5

# Check if external calibrated params exist and load them
CALIBRATED_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "calibrated_params.json")
if not os.path.exists(CALIBRATED_CONFIG_PATH):
    CALIBRATED_CONFIG_PATH = os.path.join("config", "calibrated_params.json")
if os.path.exists(CALIBRATED_CONFIG_PATH):
    try:
        with open(CALIBRATED_CONFIG_PATH, "r", encoding="utf-8") as f:
            cal_data = json.load(f)
            mb = cal_data.get("module_b_grid", {})
            if "offsets_bps" in mb:
                GRID_OFFSETS_BPS.update(mb["offsets_bps"])
            if "inventory_skew_gamma" in mb:
                INVENTORY_SKEW_GAMMA = float(mb["inventory_skew_gamma"])
            if "round_turn_spread_bps" in mb:
                MM_ROUND_TURN_SPREAD_BPS = float(mb["round_turn_spread_bps"])
            mc = cal_data.get("module_c_funding", {})
            if "funding_entry_threshold" in mc:
                FUNDING_ENTRY_THRESHOLD = float(mc["funding_entry_threshold"])
                FUNDING_SHORT_THRESHOLD = FUNDING_ENTRY_THRESHOLD
                FUNDING_LONG_THRESHOLD = -FUNDING_ENTRY_THRESHOLD
            if "funding_exit_threshold" in mc:
                FUNDING_EXIT_THRESHOLD = float(mc["funding_exit_threshold"])
            if "target_carry_notional" in mc:
                TARGET_CARRY_NOTIONAL = float(mc["target_carry_notional"])
            me = cal_data.get("module_e_stat_arb", {})
            if "entry_z_score" in me:
                STAT_ARB_ENTRY_Z = float(me["entry_z_score"])
            if "exit_z_score" in me:
                STAT_ARB_EXIT_Z = float(me["exit_z_score"])
            if "stop_loss_z_score" in me:
                STAT_ARB_STOP_Z = float(me["stop_loss_z_score"])
            if "notional_per_leg" in me:
                STAT_ARB_NOTIONAL = float(me["notional_per_leg"])
            if "lookback_periods" in me:
                STAT_ARB_LOOKBACK = int(me["lookback_periods"])
            pm = cal_data.get("position_management", {})
            if "breakeven_atr_mult" in pm:
                BREAKEVEN_ATR_MULT = float(pm["breakeven_atr_mult"])
            if "trailing_trigger_atr_mult" in pm:
                TRAILING_TRIGGER_ATR_MULT = float(pm["trailing_trigger_atr_mult"])
            if "trailing_retrace_ratio" in pm:
                TRAILING_RETRACE_RATIO = float(pm["trailing_retrace_ratio"])
            if "take_profit_atr_mult" in pm:
                TAKE_PROFIT_ATR_MULT = float(pm["take_profit_atr_mult"])
            if "stop_loss_atr_mult" in pm:
                STOP_LOSS_ATR_MULT = float(pm["stop_loss_atr_mult"])
            logger.info("Loaded external calibrated parameters from config/calibrated_params.json")
    except Exception as e:
        logger.warning(f"Could not load calibrated parameters: {e}")


def to_sdk_side(side_str: str) -> Side:
    return Side.BUY if str(side_str).upper() == "BUY" else Side.SELL


# -----------------------------------------------------------------------------
# Technical Analysis Engine
# -----------------------------------------------------------------------------
class TechnicalIndicators:
    @staticmethod
    def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
        if len(df) < 5:
            return df

        df = df.copy()

        # EMAs (Micro 20, Meso 50, Macro 100)
        df["ema_20"] = df["close"].ewm(span=20, adjust=False).mean()
        df["ema_50"] = df["close"].ewm(span=50, adjust=False).mean()
        df["ema_100"] = df["close"].ewm(span=100, adjust=False).mean()

        # RSI 14
        delta = df["close"].diff()
        gain = delta.clip(lower=0).rolling(window=14, min_periods=1).mean()
        loss = (-delta.clip(upper=0)).rolling(window=14, min_periods=1).mean()
        rs = gain / (loss.replace(0, 1e-9))
        df["rsi"] = 100.0 - (100.0 / (1.0 + rs))

        # ATR 14
        high_low = df["high"] - df["low"]
        high_close = (df["high"] - df["close"].shift()).abs()
        low_close = (df["low"] - df["close"].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df["atr"] = tr.rolling(window=14, min_periods=1).mean().fillna(0.0)

        # ADX 14
        up = df["high"].diff()
        down = -df["low"].diff()
        plus_dm = up.where((up > down) & (up > 0), 0.0)
        minus_dm = down.where((down > up) & (down > 0), 0.0)

        tr_smooth = tr.rolling(14, min_periods=1).sum().replace(0, 1e-9)
        plus_di = 100 * (plus_dm.rolling(14, min_periods=1).sum() / tr_smooth)
        minus_di = 100 * (minus_dm.rolling(14, min_periods=1).sum() / tr_smooth)
        dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-9))
        df["adx"] = dx.rolling(14, min_periods=1).mean().fillna(0.0)

        # Bollinger Bands (20, 2)
        df["bb_mid"] = df["close"].rolling(20, min_periods=1).mean()
        std = df["close"].rolling(20, min_periods=1).std().fillna(0.0)
        df["bb_upper"] = df["bb_mid"] + (2.0 * std)
        df["bb_lower"] = df["bb_mid"] - (2.0 * std)

        # Keltner Channels (20, 2x ATR)
        df["keltner_mid"] = df["close"].ewm(span=KELTNER_PERIOD, adjust=False).mean()
        df["keltner_upper"] = df["keltner_mid"] + (KELTNER_ATR_MULT * df["atr"])
        df["keltner_lower"] = df["keltner_mid"] - (KELTNER_ATR_MULT * df["atr"])

        return df


# -----------------------------------------------------------------------------
# Market Data Manager
# -----------------------------------------------------------------------------
class MarketDataManager:
    def __init__(self, symbols: List[str]):
        self.symbols = symbols
        self.price_history: Dict[str, List[Dict[str, float]]] = {s: [] for s in symbols}
        self.current_mids: Dict[str, float] = {}

    def push_tick(
        self,
        symbol: str,
        price: float,
        high: Optional[float] = None,
        low: Optional[float] = None,
        volume: Optional[float] = None,
    ):
        if not price or price <= 0:
            return
        self.current_mids[symbol] = price
        record = {
            "close": price,
            "high": high if high is not None else price,
            "low": low if low is not None else price,
            "volume": volume if volume is not None else 0.0,
        }
        self.price_history[symbol].append(record)
        if len(self.price_history[symbol]) > 600:
            self.price_history[symbol].pop(0)

    def get_dataframe(self, symbol: str) -> pd.DataFrame:
        data = self.price_history.get(symbol, [])
        if not data:
            return pd.DataFrame(columns=["close", "high", "low", "volume"])
        return pd.DataFrame(data)


# -----------------------------------------------------------------------------
# Core Quantitative Trading Agent
# -----------------------------------------------------------------------------
class QuantitativeTradingAgent:
    def __init__(self, client: GodarkClient, rest_client: GodarkRestClient):
        self.client = client
        self.rest_client = rest_client
        self.md_manager = MarketDataManager(SYMBOLS)
        self.md_client: Optional[MarketDataClient] = None

        self.positions: Dict[str, Dict] = {}
        self.open_grid_orders: Dict[str, List[str]] = {s: [] for s in SYMBOLS}
        self._ref_fed: Set[str] = set()
        self.system_health_accepting: bool = True
        self.running: bool = True

        self.active_modules: Dict[str, str] = {}
        self.funding_rates: Dict[str, float] = {}
        self.oi_history: Dict[str, Deque[Tuple[float, float]]] = {s: deque(maxlen=300) for s in SYMBOLS}
        self.volume_history: Dict[str, Deque[Tuple[float, float]]] = {s: deque(maxlen=300) for s in SYMBOLS}
        self.telegram = telegram_notifier
        self.trade_logger = TradeLogger("trades.log")

        # Fee Schedule (initialized to Core tier; updated dynamically via VIP API)
        self.maker_fee_pct: float = DEFAULT_MAKER_FEE_PCT
        self.taker_fee_pct: float = DEFAULT_TAKER_FEE_PCT

        # Grid state & churn reduction tracking: {symbol: {"mid": float, "time": float}}
        self.grid_state: Dict[str, Dict] = {s: {"mid": 0.0, "time": 0.0} for s in SYMBOLS}

        # Regime stability hysteresis: {symbol: (pending_regime, consecutive_ticks)}
        self.regime_pending: Dict[str, Tuple[str, int]] = {}

        # Capital & Trade frequency management
        self.cached_collateral: float = 1000000.0
        self.last_trade_time: Dict[str, float] = {s: 0.0 for s in SYMBOLS}
        self.position_peak_pnl: Dict[str, float] = {}

        # Delta-Neutral Stat-Arb state (ETH/BTC Pair Engine)
        self.eth_btc_ratios: Deque[Tuple[float, float]] = deque(maxlen=STAT_ARB_LOOKBACK * 2)
        self.stat_arb_active: Optional[str] = None  # None, "LONG_ETH_SHORT_BTC", "SHORT_ETH_LONG_BTC"
        self.stat_arb_entry_z: float = 0.0
        self.position_source_module: Dict[str, str] = {}  # Tracks originating module for each position (e.g. "B", "C", "E")

    # -------------------------------------------------------------------------
    # Formatting Helpers
    # -------------------------------------------------------------------------
    @staticmethod
    def format_price(symbol: str, price: float, rounding=ROUND_HALF_UP) -> str:
        dec = DECIMALS_MAP.get(symbol)
        p_dp = dec.price_decimals if dec else 2
        d = Decimal(str(price))
        pattern = "0." + "0" * p_dp if p_dp > 0 else "0"
        return str(d.quantize(Decimal(pattern), rounding=rounding))

    @staticmethod
    def format_qty(symbol: str, qty: float) -> str:
        dec = DECIMALS_MAP.get(symbol)
        q_dp = dec.quantity_decimals if dec else 3
        d = Decimal(str(qty))
        pattern = "0." + "0" * q_dp if q_dp > 0 else "0"
        return str(d.quantize(Decimal(pattern), rounding=ROUND_DOWN))

    # -------------------------------------------------------------------------
    # Account & Risk Management
    # -------------------------------------------------------------------------
    async def get_collateral_balance(self) -> float:
        try:
            acct = await self.rest_client.get_account()
            if acct:
                summary = getattr(acct, "summary", None) or (acct.get("summary") if isinstance(acct, dict) else None)
                if summary:
                    free = getattr(summary, "free_collateral", None) or (summary.get("free_collateral") if isinstance(summary, dict) else None)
                    total = getattr(summary, "total_collateral", None) or (summary.get("total_collateral") if isinstance(summary, dict) else None)
                    val = float(free or total or 0)
                    if val > 0:
                        self.cached_collateral = val
                        return val
        except Exception as e:
            logger.warning(f"Could not fetch account collateral via REST: {e}")
        return self.cached_collateral

    async def sync_positions(self, _retried: bool = False):
        try:
            positions_data = await self.rest_client.get_positions()
            if positions_data:
                items = (
                    positions_data
                    if isinstance(positions_data, list)
                    else getattr(positions_data, "rows", None)
                    or getattr(positions_data, "positions", [])
                    or []
                )
                new_positions = {}
                for pos in items:
                    sym = pos.get("symbol") if isinstance(pos, dict) else getattr(pos, "symbol", None)
                    if not sym:
                        sid = pos.get("symbol_id") if isinstance(pos, dict) else getattr(pos, "symbol_id", None)
                        if sid:
                            sym = SYMBOL_BY_ID.get(sid)
                    size = float(pos.get("size", 0) if isinstance(pos, dict) else getattr(pos, "size", 0) or 0)
                    notional = float(pos.get("notional", 0) if isinstance(pos, dict) else getattr(pos, "notional", 0) or 0)
                    entry_price = float(pos.get("entry_price", 0) if isinstance(pos, dict) else getattr(pos, "entry_price", 0) or 0)
                    side_raw = pos.get("side") if isinstance(pos, dict) else getattr(pos, "side", None)
                    side_str = str(side_raw.value if hasattr(side_raw, "value") else side_raw or "").upper()
                    side = "SELL" if "SELL" in side_str else "BUY"
                    if sym and abs(size) > 0:
                        new_positions[sym] = {
                            "symbol": sym,
                            "size": size,
                            "notional": notional if notional != 0 else size * entry_price,
                            "entry_price": entry_price,
                            "side": side,
                        }
                self.positions = new_positions
        except Exception as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status == 401 and not _retried:
                logger.warning("REST auth rejected (401) on positions sync - refreshing token and retrying once.")
                try:
                    await self.rest_client.connect()
                    return await self.sync_positions(_retried=True)
                except Exception as e2:
                    logger.warning(f"REST re-auth failed: {e2}")
                    return
            logger.warning(f"Could not sync positions via REST: {e}")

    def _check_portfolio_heat(self) -> bool:
        try:
            total_notional = sum(abs(pos.get("notional", 0.0)) for pos in self.positions.values())
            # Enforce conservative institutional ceiling of $400k max total exposure on $1M pool
            return total_notional < MAX_PORTFOLIO_NOTIONAL
        except Exception:
            return True

    def _get_active_module_count(self, module: str) -> int:
        return sum(1 for m in self.active_modules.values() if m == module)

    async def calculate_risk_position_size(self, symbol: str, entry_price: float, atr: float) -> float:
        collateral = await self.get_collateral_balance()
        # 0.20% portfolio risk per trade = ~$2,000 risk budget on $1M pool
        risk_budget = collateral * RISK_FACTOR

        # Volatility floor: at least MIN_ATR_RATIO (0.6%) of price to prevent micro-stops on 2s ticks
        min_atr = entry_price * MIN_ATR_RATIO
        effective_atr = max(atr if not pd.isna(atr) else 0.0, min_atr)
        sl_distance = 1.5 * effective_atr

        # Deduct estimated round-trip fee buffer from risk budget
        fee_buffer_mult = (self.maker_fee_pct + self.taker_fee_pct) * DEFAULT_LEVERAGE
        net_risk_budget = max(risk_budget * (1.0 - fee_buffer_mult), risk_budget * 0.8)

        raw_qty = net_risk_budget / sl_distance

        # Size caps for institutional 30% APR goal:
        # Cap at MAX_SINGLE_TRADE_NOTIONAL ($120k) and remaining portfolio heat budget
        current_notional = sum(abs(p.get("notional", 0.0)) for p in self.positions.values())
        remaining_heat = max(0.0, MAX_PORTFOLIO_NOTIONAL - current_notional)
        allowed_notional = min(MAX_SINGLE_TRADE_NOTIONAL, remaining_heat)
        max_qty = allowed_notional / entry_price
        min_notional_qty = MIN_NOTIONAL_USD / entry_price

        final_qty = min(max(raw_qty, min_notional_qty), max_qty)
        dec = DECIMALS_MAP.get(symbol)
        min_qty = 10 ** (-(dec.quantity_decimals if dec else 3))
        return max(final_qty, min_qty)

    async def _set_leverage(self, symbol: str, leverage: int) -> bool:
        try:
            ack = await self.client.update_leverage(symbol, leverage)
            success = getattr(ack, "success", True)
            if not success:
                error = getattr(ack, "error_code", None) or getattr(ack, "error", None)
                logger.warning(f"[{symbol}] Leverage change to {leverage}x rejected: {error}")
                return False
            return True
        except Exception as e:
            logger.warning(f"[{symbol}] Leverage update to {leverage}x failed: {e}")
            return False

    async def _startup_order_sweep(self) -> int:
        """Cancel every resting order left behind by a previous run."""
        try:
            ack = await self.client.cancel_all_orders()
            count = getattr(ack, "count", 0) or 0
            if getattr(ack, "error_code", None):
                logger.warning(f"Startup order sweep error: {getattr(ack, 'reject_text', None) or ack.error_code}")
            if count:
                logger.info(f"Startup sweep: cancelled {count} resting order(s) left by a previous run.")
            else:
                logger.info("Startup sweep: no resting orders found.")
            return count
        except Exception as e:
            logger.warning(f"Startup order sweep failed: {e}")
            return 0

    async def _send_startup_alert(self, swept: int):
        if not self.telegram.enabled:
            logger.info("Telegram disabled (no credentials) - skipping startup alert.")
            return
        source = "GitHub Actions" if os.getenv("GITHUB_ACTIONS", "").lower() == "true" else "local"
        run_info = os.getenv("GITHUB_RUN_ID", "")
        if run_info:
            run_info = f"\n<b>Run:</b> {os.getenv('GITHUB_SERVER_URL', '')}/{os.getenv('GITHUB_REPOSITORY', '')}/actions/runs/{run_info}"
        msg = (
            "▶️ <b>Quant Agent Started</b>\n"
            f"<b>Source:</b> {source}\n"
            f"<b>Symbols:</b> {', '.join(SYMBOLS)}\n"
            f"<b>Startup sweep:</b> {swept} leftover order(s) cancelled\n"
            f"{run_info}\n"
            f"<b>Time:</b> {datetime.utcnow().strftime('%H:%M:%S UTC')}"
        )
        ok = await self.telegram.send(msg)
        logger.info(f"Startup Telegram alert {'sent' if ok else 'FAILED'}.")

    async def _send_fatal_alert(self, text: str):
        if self.telegram.enabled:
            await self.telegram.send(f"🚨 <b>Quant Agent FATAL</b>\n{text}")

    # -------------------------------------------------------------------------
    # Order Routing & Execution
    # -------------------------------------------------------------------------
    async def place_directional_order(
        self, symbol: str, side: str, price: float, qty: float, atr: float, module: str = "A"
    ):
        effective_atr = max(atr if not pd.isna(atr) else 0.0, price * MIN_ATR_RATIO)
        sl = price - (1.5 * effective_atr) if side == "BUY" else price + (1.5 * effective_atr)

        # Fee-adjusted TP target: 3.0 * ATR + round-trip fee drag to guarantee positive expectancy
        fee_drag = price * (self.maker_fee_pct * 2.0)
        tp = price + (3.0 * effective_atr) + fee_drag if side == "BUY" else price - (3.0 * effective_atr) - fee_drag

        # Fee & Funding Pre-Trade Gate:
        expected_profit = abs(tp - price) * qty
        expected_fees = (price * qty) * (self.maker_fee_pct * 2.0)

        # Funding rate cash flow: if position collects funding or is Carry/Stat-Arb, funding is yield (NOT friction!)
        funding_rate = self.funding_rates.get(symbol, 0.0)
        is_earning_funding = (side == "SELL" and funding_rate > 0) or (side == "BUY" and funding_rate < 0)
        if is_earning_funding or module in ("C", "E"):
            expected_funding_cost = 0.0
        else:
            expected_funding_cost = (price * qty) * abs(funding_rate) * 2.0

        total_friction = expected_fees + expected_funding_cost
        if expected_profit > 0 and (total_friction / expected_profit) > MAX_FEE_TO_PROFIT_RATIO:
            logger.info(
                f"[{symbol}] MODULE {module} trade blocked: total friction ${total_friction:.2f} "
                f"exceeds {MAX_FEE_TO_PROFIT_RATIO:.0%} of expected profit ${expected_profit:.2f}"
            )
            return False

        # Maker Limit Pricing: offset price by MAKER_OFFSET_BPS (3 bps) passive
        # to guarantee execution as MAKER (0.015% fee) instead of crossing as TAKER (0.055% fee)
        if side == "BUY":
            target_price = price * (1.0 - MAKER_OFFSET_BPS)
            p_str = self.format_price(symbol, target_price, rounding=ROUND_DOWN)
        else:
            target_price = price * (1.0 + MAKER_OFFSET_BPS)
            p_str = self.format_price(symbol, target_price, rounding=ROUND_UP)

        q_str = self.format_qty(symbol, qty)
        sl_str = self.format_price(symbol, sl)
        tp_str = self.format_price(symbol, tp)

        opts = PlaceOrderOptions(
            stop_loss_price=sl_str,
            take_profit_price=tp_str,
            post_only=True,
            stp_mode="CANCEL_AGGRESSOR",
        )

        logger.info(
            f"[{symbol}] MODULE {module} Signal (Maker) -> Side: {side} | Price: {p_str} | Qty: {q_str} | "
            f"SL: {sl_str} | TP: {tp_str}"
        )

        if DRY_RUN:
            logger.info(f"[{symbol}] DRY_RUN enabled. Order execution simulated.")
            order_id = "DRY_RUN_" + str(int(time.time()))
            self.position_source_module[symbol] = module
            regime = self._detect_regime(symbol)
            self.trade_logger.log_trade(
                symbol=symbol, side=side, price=price, qty=qty,
                module=module, order_id=order_id,
                sl=float(sl_str), tp=float(tp_str), regime=regime
            )
            if self.telegram.enabled:
                msg = self.telegram.format_trade(
                    symbol=symbol, side=side, price=price, qty=qty,
                    module=module, order_id=order_id,
                    sl=float(sl_str), tp=float(tp_str)
                )
                await self.telegram.send(msg)
            return True

        try:
            ack = await self.client.place_order(
                symbol=symbol,
                side=to_sdk_side(side),
                order_type=OrderType.LIMIT,
                quantity=q_str,
                price=p_str,
                time_in_force=TimeInForce.GTC,
                options=opts,
            )

            success = getattr(ack, "success", True)
            order_id = getattr(ack, "order_id", None) or (ack.get("order_id") if isinstance(ack, dict) else None)
            error = getattr(ack, "error", None) or getattr(ack, "error_code", None)

            # Retry once with slightly wider passive offset if post_only would cross
            if not success:
                err_str = str(error or "").upper()
                if "POST_ONLY" in err_str or "CROSS" in err_str:
                    logger.info(f"[{symbol}] Maker post_only would cross, retrying once with 6 bps passive offset...")
                    if side == "BUY":
                        retry_price = price * (1.0 - 0.0006)
                        retry_p_str = self.format_price(symbol, retry_price, rounding=ROUND_DOWN)
                    else:
                        retry_price = price * (1.0 + 0.0006)
                        retry_p_str = self.format_price(symbol, retry_price, rounding=ROUND_UP)
                    ack = await self.client.place_order(
                        symbol=symbol,
                        side=to_sdk_side(side),
                        order_type=OrderType.LIMIT,
                        quantity=q_str,
                        price=retry_p_str,
                        time_in_force=TimeInForce.GTC,
                        options=opts,
                    )
                    success = getattr(ack, "success", True)
                    order_id = getattr(ack, "order_id", None) or (ack.get("order_id") if isinstance(ack, dict) else None)
                    error = getattr(ack, "error", None) or getattr(ack, "error_code", None)

            if not success:
                logger.error(f"[{symbol}] Order rejected by venue: error={error}")
                return False

            logger.info(f"[{symbol}] Order successfully accepted: order_id={order_id}")
            self.last_trade_time[symbol] = time.time()
            self.position_source_module[symbol] = module
            regime = self._detect_regime(symbol)

            self.trade_logger.log_trade(
                symbol=symbol, side=side, price=price, qty=qty,
                module=module, order_id=str(order_id) if order_id else None,
                sl=float(sl_str), tp=float(tp_str), regime=regime
            )
            if self.telegram.enabled:
                msg = self.telegram.format_trade(
                    symbol=symbol, side=side, price=price, qty=qty,
                    module=module, order_id=str(order_id) if order_id else None,
                    sl=float(sl_str), tp=float(tp_str)
                )
                await self.telegram.send(msg)
            return True
        except OrderError as e:
            logger.error(f"[{symbol}] Order Error ({getattr(e, 'error_code', 'N/A')}): {e}")
            return False
        except Exception as e:
            logger.error(f"[{symbol}] Unexpected Error placing order: {e}", exc_info=True)
            return False

    async def close_position(self, symbol: str, reason: str = "exit") -> bool:
        """Actively close an open position with reduce_only to harvest profit or cut loss."""
        pos = self.positions.get(symbol)
        if not pos:
            return False

        size = abs(float(pos["size"]))
        pos_side = str(pos.get("side", "")).upper()
        # Closing a BUY/LONG position requires SELL. Closing a SELL/SHORT position requires BUY.
        side = "SELL" if pos_side == "BUY" else "BUY"
        mid_price = self.md_manager.current_mids.get(symbol)
        if not mid_price or mid_price <= 0:
            return False

        # 1. Clean up any resting open orders for this symbol first so reduce-only is never blocked
        try:
            await self.client.cancel_all_orders(symbol)
            self.open_grid_orders[symbol] = []
            await asyncio.sleep(0.3)
        except Exception as e:
            logger.debug(f"[{symbol}] Cancel orders before close notice: {e}")

        q_str = self.format_qty(symbol, size)

        # 2. Try passive Maker exit first to capture maker fees (0.015%)
        if side == "BUY":
            target_price = mid_price * (1.0 - MAKER_OFFSET_BPS)
            p_str = self.format_price(symbol, target_price, rounding=ROUND_DOWN)
        else:
            target_price = mid_price * (1.0 + MAKER_OFFSET_BPS)
            p_str = self.format_price(symbol, target_price, rounding=ROUND_UP)

        opts_maker = PlaceOrderOptions(reduce_only=True, post_only=True, stp_mode="CANCEL_AGGRESSOR")

        logger.info(
            f"[{symbol}] Routing position close: {side} {q_str} (closing {pos_side} position) | price={p_str} | reason='{reason}'"
        )

        if DRY_RUN:
            logger.info(f"[{symbol}] DRY_RUN enabled. Position close simulated (reason='{reason}').")
            self.last_trade_time[symbol] = time.time()
            self.position_peak_pnl.pop(f"{symbol}_{pos.get('side', '')}", None)
            self.position_source_module.pop(symbol, None)
            self.positions.pop(symbol, None)
            return True

        try:
            # 2. Try passive Maker exit first to capture maker fees (0.015%)
            try:
                ack = await self.client.place_order(
                    symbol=symbol,
                    side=to_sdk_side(side),
                    order_type=OrderType.LIMIT,
                    quantity=q_str,
                    price=p_str,
                    time_in_force=TimeInForce.GTC,
                    options=opts_maker,
                )
                success = getattr(ack, "success", True)
                oid = getattr(ack, "order_id", None) or (ack.get("order_id") if isinstance(ack, dict) else None)
            except OrderError as e:
                logger.info(f"[{symbol}] Maker close rejected/crossed ({e}). Routing IOC reduce_only...")
                success = False
                oid = None

            # 3. If maker post_only crossed or rejected, fallback to aggressive IOC reduce_only
            if not success or not oid:
                try:
                    logger.info(f"[{symbol}] Routing IOC reduce_only...")
                    opts_ioc = PlaceOrderOptions(reduce_only=True, post_only=False, stp_mode="CANCEL_AGGRESSOR")
                    agg_price = mid_price * (1.002 if side == "BUY" else 0.998)
                    ack = await self.client.place_order(
                        symbol=symbol,
                        side=to_sdk_side(side),
                        order_type=OrderType.LIMIT,
                        quantity=q_str,
                        price=self.format_price(symbol, agg_price),
                        time_in_force=TimeInForce.IOC,
                        options=opts_ioc,
                    )
                    success = getattr(ack, "success", True)
                    oid = getattr(ack, "order_id", None) or (ack.get("order_id") if isinstance(ack, dict) else None)
                except OrderError as e:
                    logger.info(f"[{symbol}] IOC reduce_only rejected ({e}). Executing exchange-native close_all...")
                    success = False
                    oid = None

            # 4. If neither Maker nor IOC filled, execute exchange-native close_all
            if not success or not oid:
                logger.info(f"[{symbol}] Fallback to exchange-native close_all({symbol})...")
                await self.client.cancel_all_orders(symbol)
                await asyncio.sleep(0.3)
                close_ack = await self.client.close_all(symbol)
                success = True
                oid = getattr(close_ack, "count", 1) or "NATIVE_CLOSE_ALL"

            if success and oid:
                logger.info(f"[{symbol}] Position closed successfully: order_id={oid} reason='{reason}'")
                self.last_trade_time[symbol] = time.time()
                self.position_peak_pnl.pop(f"{symbol}_{pos.get('side', '')}", None)
                self.position_source_module.pop(symbol, None)
                if self.telegram.enabled:
                    entry = float(pos.get("entry_price", mid_price))
                    pnl_pct = (mid_price - entry) / entry if pos["size"] > 0 else (entry - mid_price) / entry
                    msg = self.telegram.format_close(
                        symbol=symbol,
                        side=side,
                        price=mid_price,
                        qty=size,
                        reason=reason,
                        unrealized_pnl=pnl_pct * (entry * size),
                    )
                    await self.telegram.send(msg)

                await asyncio.sleep(1.0)
                await self.sync_positions()
                return True

        except OrderError as e:
            logger.info(f"[{symbol}] OrderError during close cascade ({e}). Executing emergency native close_all({symbol})...")
            try:
                await self.client.cancel_all_orders(symbol)
                await asyncio.sleep(0.3)
                close_ack = await self.client.close_all(symbol)
                count = getattr(close_ack, "count", 0)
                logger.info(f"[{symbol}] Native close_all executed successfully: count={count}")
                self.last_trade_time[symbol] = time.time()
                self.position_peak_pnl.pop(f"{symbol}_{pos.get('side', '')}", None)
                self.position_source_module.pop(symbol, None)
                await asyncio.sleep(1.0)
                await self.sync_positions()
                return True
            except Exception as e2:
                logger.error(f"[{symbol}] Native close_all failed: {e2}")
        except (GDXConnectionError, GDXTimeoutError, ConnectionError, OSError) as e:
            logger.warning(f"[{symbol}] Transient connection drop during close ({e}). Reconnect in progress...")
        except Exception as e:
            logger.error(f"[{symbol}] Unexpected exception executing position close: {e}", exc_info=True)
        return False

    async def manage_open_position(self, symbol: str, mid_price: float) -> str:
        """Institutional active position manager: trails profits, triggers TP, cuts losses, and exits on reversals."""
        pos = self.positions.get(symbol)
        if not pos or not mid_price or mid_price <= 0:
            return "no_position"

        entry = float(pos["entry_price"])
        size = abs(float(pos["size"]))
        side = pos.get("side", "BUY" if pos["size"] > 0 else "SELL")

        df = self.md_manager.get_dataframe(symbol)
        if len(df) < 20:
            return "warming_up"

        df = TechnicalIndicators.calculate_indicators(df)
        latest = df.iloc[-1]
        calc_atr = float(latest.get("atr", 0.0) or 0.0)
        atr = max(calc_atr, entry * MIN_ATR_RATIO)

        # Unrealized PnL percentage and USD amount
        pnl_pct = (mid_price - entry) / entry if side == "BUY" else (entry - mid_price) / entry
        unrealized_usd = pnl_pct * (entry * size)

        # Track Peak Favorable Excursion (PFE) for trailing stops
        peak_key = f"{symbol}_{side}"
        self.position_peak_pnl[peak_key] = max(self.position_peak_pnl.get(peak_key, 0.0), pnl_pct)
        peak_pnl = self.position_peak_pnl[peak_key]

        atr_pct = atr / entry
        source_module = self.position_source_module.get(symbol, self.active_modules.get(symbol, "B"))

        # 0. Market Making (Module B) Fast Round-Turn Exit:
        # If position originated from MM grid quoting, take profit at MM_ROUND_TURN_SPREAD_BPS (+20 bps)
        # to immediately capture maker spread and neutralize inventory back to zero.
        if source_module == "B" or (entry * size) < 10000.0:
            if pnl_pct >= MM_ROUND_TURN_SPREAD_BPS:
                logger.info(
                    f"[{symbol}] [MM_ROUND_TURN] Target +{pnl_pct:.2%} hit (+${unrealized_usd:,.2f}). "
                    f"Locking in market making spread profit!"
                )
                closed = await self.close_position(symbol, reason=f"mm_round_turn_+{pnl_pct:.2%}")
                if closed:
                    return "mm_round_turn_closed"

        # 0B. Funding Carry (Module C) Normalization Exit:
        funding = self.funding_rates.get(symbol, 0.0)
        if source_module == "C":
            if abs(funding) < FUNDING_EXIT_THRESHOLD:
                logger.info(
                    f"[{symbol}] [CARRY_HARVEST] Funding normalized ({funding:.6f} < {FUNDING_EXIT_THRESHOLD:.6f}). "
                    f"Harvesting carry (+${unrealized_usd:,.2f})..."
                )
                closed = await self.close_position(symbol, reason=f"funding_normalized_{funding:.6f}")
                if closed:
                    return "carry_normalized_closed"

        # 1. Take Profit: Harvest at calibrated TP threshold (e.g. 2.2x ATR)
        tp_threshold = TAKE_PROFIT_ATR_MULT * atr_pct
        if pnl_pct >= tp_threshold:
            logger.info(
                f"[{symbol}] [TAKE_PROFIT] TARGET HIT: PnL={pnl_pct:.2%} (+${unrealized_usd:,.2f}) "
                f"reached threshold {tp_threshold:.2%}. Harvesting profit!"
            )
            closed = await self.close_position(symbol, reason=f"take_profit_+{pnl_pct:.2%}")
            if closed:
                return "harvested_profit"

        # 2. Level 2 Dynamic Trailing Stop (High-Water Mark Retracement):
        # Once position achieves >= TRAILING_TRIGGER_ATR_MULT (1.25x ATR), allow max 35% retrace from peak
        if peak_pnl >= (TRAILING_TRIGGER_ATR_MULT * atr_pct) and pnl_pct < (peak_pnl * (1.0 - TRAILING_RETRACE_RATIO)):
            logger.info(
                f"[{symbol}] [TRAILING_STOP] TRIGGERED: peak was {peak_pnl:.2%}, retraced to {pnl_pct:.2%}. "
                f"Locking in +${unrealized_usd:,.2f} profit!"
            )
            closed = await self.close_position(symbol, reason=f"trailing_stop_lock_+{pnl_pct:.2%}")
            if closed:
                return "trailing_stop_closed"

        # 3. Level 1 Breakeven Lock with Fee Buffer:
        # If position gained >= BREAKEVEN_ATR_MULT (0.75x ATR) and drops back to 6 bps (+0.06%), exit to guarantee covering fees
        if peak_pnl >= (BREAKEVEN_ATR_MULT * atr_pct) and pnl_pct <= 0.0006:
            logger.info(
                f"[{symbol}] [BREAKEVEN_LOCK] TRIGGERED: peak was {peak_pnl:.2%}, pulled back to {pnl_pct:.2%}. "
                f"Exiting at fee breakeven (+${unrealized_usd:,.2f}) to preserve capital!"
            )
            closed = await self.close_position(symbol, reason=f"breakeven_lock_+{pnl_pct:.2%}")
            if closed:
                return "breakeven_closed"

        # 4. Protective Stop Loss: Cut loss if trade drops to -STOP_LOSS_ATR_MULT (1.5x ATR)
        sl_threshold = STOP_LOSS_ATR_MULT * atr_pct
        if pnl_pct <= -sl_threshold:
            logger.info(
                f"[{symbol}] [STOP_LOSS] HIT: PnL={pnl_pct:.2%} (-${abs(unrealized_usd):,.2f}). "
                f"Cutting loss to preserve capital!"
            )
            closed = await self.close_position(symbol, reason=f"stop_loss_{pnl_pct:.2%}")
            if closed:
                return "stop_loss_closed"

        # 4. Momentum / Trend Invalidation:
        ema_20, ema_50 = float(latest["ema_20"]), float(latest["ema_50"])
        rsi = float(latest["rsi"]) if not pd.isna(latest["rsi"]) else 50.0

        if side == "SELL" and (ema_20 > ema_50) and rsi > 58.0 and pnl_pct < 0.002:
            logger.info(f"[{symbol}] [REVERSAL] Bullish reversal detected against SHORT (RSI={rsi:.1f}). Closing early...")
            closed = await self.close_position(symbol, reason="trend_reversal_exit")
            if closed:
                return "reversal_closed"

        elif side == "BUY" and (ema_20 < ema_50) and rsi < 42.0 and pnl_pct < 0.002:
            logger.info(f"[{symbol}] [REVERSAL] Bearish reversal detected against LONG (RSI={rsi:.1f}). Closing early...")
            closed = await self.close_position(symbol, reason="trend_reversal_exit")
            if closed:
                return "reversal_closed"

        # 5. Funding Headwind Invalidation (Carry Protection)
        funding = self.funding_rates.get(symbol, 0.0)
        if side == "SELL" and funding < -0.00010:
            logger.info(f"[{symbol}] Funding turned punitive ({funding:.6f}) against SHORT. Closing carry...")
            closed = await self.close_position(symbol, reason="funding_flip_exit")
            if closed:
                return "funding_flip_closed"
        elif side == "BUY" and funding > 0.00010:
            logger.info(f"[{symbol}] Funding turned punitive ({funding:.6f}) against LONG. Closing carry...")
            closed = await self.close_position(symbol, reason="funding_flip_exit")
            if closed:
                return "funding_flip_closed"

        return f"active_{side}_pnl={pnl_pct:.2%}_(+${unrealized_usd:,.2f})"

    async def clear_grid_orders(self, symbol: str):
        if not DRY_RUN:
            try:
                await self.client.cancel_all_orders(symbol)
            except Exception as e:
                logger.debug(f"[{symbol}] Error cancelling grid orders: {e}")
        self.open_grid_orders[symbol] = []

    async def execute_grid_module(self, symbol: str, mid_price: float):
        if symbol in self.positions:
            return "has_active_position"
        await self._switch_module(symbol, "B")
        config = REGIME_CONFIG["MEAN_REV"]
        if self._get_active_module_count("B") > config["max_pos"]:
            return "blocked_max_pos"
        if not self._check_portfolio_heat():
            return "blocked_heat"

        # Grid Churn Reduction:
        # If orders are already resting, price moved < 15 bps, and placed < 90s ago, keep resting
        last_grid = self.grid_state.get(symbol, {})
        last_mid = last_grid.get("mid", 0.0)
        last_time = last_grid.get("time", 0.0)
        active_ids = self.open_grid_orders.get(symbol, [])
        now_ts = time.time()

        if active_ids and last_mid > 0:
            price_drift = abs(mid_price - last_mid) / last_mid
            if price_drift < 0.0015 and (now_ts - last_time) < 90.0:
                logger.debug(f"[{symbol}] Grid resting orders stable (drift={price_drift:.2%}), skipping re-quote.")
                return "grid_stable"

        await self.clear_grid_orders(symbol)
        await self._set_leverage(symbol, config["leverage"])

        dec = DECIMALS_MAP.get(symbol)
        min_qty = 10 ** (-(dec.quantity_decimals if dec else 3))

        # Dynamic Leg Sizing: Scale to 0.35% of collateral (~$3,500 per leg on $1M pool)
        collateral = await self.get_collateral_balance()
        target_leg_notional = max(1000.0, min(5000.0, collateral * 0.0035))
        qty = max(min_qty, target_leg_notional / mid_price)
        q_str = self.format_qty(symbol, qty)

        # Dynamic ATR & Dense 3-tier calibrated symbol offsets:
        cal_offsets = GRID_OFFSETS_BPS.get(symbol, [0.0018, 0.0036, 0.0060])
        df = self.md_manager.get_dataframe(symbol)
        atr = mid_price * MIN_ATR_RATIO
        if len(df) >= 14:
            df_ind = TechnicalIndicators.calculate_indicators(df)
            calc_atr = float(df_ind.iloc[-1].get("atr", 0.0) or 0.0)
            if calc_atr > 0:
                atr = max(calc_atr, mid_price * MIN_ATR_RATIO)

        atr_pct = atr / mid_price
        offset_1 = max(cal_offsets[0], 0.20 * atr_pct)
        offset_2 = max(cal_offsets[1] if len(cal_offsets) > 1 else offset_1 * 2.0, offset_1 * 1.8)
        offsets = [offset_1, offset_2]
        if len(cal_offsets) >= 3:
            offsets.append(max(cal_offsets[2], offset_2 * 1.5))

        # Avellaneda-Stoikov Inventory Skew:
        # Skew quoting mid-price based on net portfolio directional exposure
        net_portfolio_delta = sum(
            abs(p.get("notional", 0.0)) if p.get("side") == "BUY" else -abs(p.get("notional", 0.0))
            for p in self.positions.values()
        )
        skew_fraction = max(-1.0, min(1.0, net_portfolio_delta / max(1.0, MAX_PORTFOLIO_NOTIONAL)))
        skew_bps = skew_fraction * (INVENTORY_SKEW_GAMMA * 10.0)
        skewed_mid = mid_price * (1.0 - skew_bps)

        new_order_ids = []

        for offset in offsets:
            bid_price = skewed_mid * (1.0 - offset)
            ask_price = skewed_mid * (1.0 + offset)

            bid_str = self.format_price(symbol, bid_price, rounding=ROUND_DOWN)
            ask_str = self.format_price(symbol, ask_price, rounding=ROUND_UP)

            opts = PlaceOrderOptions(post_only=True, stp_mode="CANCEL_AGGRESSOR")

            if not DRY_RUN:
                try:
                    b_ack = await self.client.place_order(
                        symbol=symbol,
                        side=Side.BUY,
                        order_type=OrderType.LIMIT,
                        quantity=q_str,
                        price=bid_str,
                        time_in_force=TimeInForce.GTC,
                        options=opts,
                    )
                    b_id = getattr(b_ack, "order_id", None)
                    if getattr(b_ack, "success", True) and b_id:
                        new_order_ids.append(str(b_id))
                    else:
                        logger.warning(
                            f"[{symbol}] Grid BUY ack failed: {getattr(b_ack, 'error_code', None) or getattr(b_ack, 'error', None)}"
                        )
                except OrderError as e:
                    logger.error(f"[{symbol}] Grid Buy Error ({getattr(e, 'error_code', 'N/A')}): {e}")
                except (GDXConnectionError, GDXTimeoutError, ConnectionError, OSError) as e:
                    logger.warning(f"[{symbol}] Grid Buy connection notice: {e}")
                    break

                try:
                    a_ack = await self.client.place_order(
                        symbol=symbol,
                        side=Side.SELL,
                        order_type=OrderType.LIMIT,
                        quantity=q_str,
                        price=ask_str,
                        time_in_force=TimeInForce.GTC,
                        options=opts,
                    )
                    a_id = getattr(a_ack, "order_id", None)
                    if getattr(a_ack, "success", True) and a_id:
                        new_order_ids.append(str(a_id))
                    else:
                        logger.warning(
                            f"[{symbol}] Grid SELL ack failed: {getattr(a_ack, 'error_code', None) or getattr(a_ack, 'error', None)}"
                        )
                except OrderError as e:
                    logger.error(f"[{symbol}] Grid Sell Error ({getattr(e, 'error_code', 'N/A')}): {e}")
                except (GDXConnectionError, GDXTimeoutError, ConnectionError, OSError) as e:
                    logger.warning(f"[{symbol}] Grid Sell connection notice: {e}")
                    break
            else:
                new_order_ids.extend(["SIM_BID_" + bid_str, "SIM_ASK_" + ask_str])

        self.open_grid_orders[symbol] = new_order_ids
        self.grid_state[symbol] = {"mid": mid_price, "time": now_ts}

        if new_order_ids:
            self.trade_logger.log_grid(
                symbol=symbol,
                bid_price=mid_price * (1.0 - offset_1),
                ask_price=mid_price * (1.0 + offset_1),
                qty=qty,
                order_ids=new_order_ids,
                module="B",
            )
            if self.telegram.enabled:
                msg = self.telegram.format_grid(
                    symbol=symbol,
                    bid_price=mid_price * (1.0 - offset_1),
                    ask_price=mid_price * (1.0 + offset_1),
                    qty=qty,
                    order_ids=new_order_ids,
                )
                await self.telegram.send(msg)
            return "grid_placed"
        return "grid_empty"

    # -------------------------------------------------------------------------
    # Strategy Modules
    # -------------------------------------------------------------------------
    async def execute_trend_module(self, symbol: str, mid_price: float):
        if symbol in self.positions:
            return "already_positioned"

        df = self.md_manager.get_dataframe(symbol)
        if len(df) < 20:
            return "warming_up"

        df = TechnicalIndicators.calculate_indicators(df)
        latest = df.iloc[-1]
        prev = df.iloc[-2]

        close = float(latest["close"])
        ema_20, ema_50 = float(latest["ema_20"]), float(latest["ema_50"])
        prev_ema20, prev_ema50 = float(prev["ema_20"]), float(prev["ema_50"])
        rsi = float(latest["rsi"]) if not pd.isna(latest["rsi"]) else 50.0
        calc_atr = float(latest["atr"]) if not pd.isna(latest["atr"]) else 0.0
        atr = max(calc_atr, close * MIN_ATR_RATIO)
        adx = float(latest.get("adx", 0.0) or 0.0)
        funding = self.funding_rates.get(symbol, 0.0)

        # Trend Confirmation: Allow trends when ADX >= 14.0 (captures real intraday directional expansions)
        if adx < 14.0:
            return "low_adx"

        # EMA Separation Buffer: 0.05x ATR (eliminates micro-touches while capturing real trend legs)
        ema_spread = abs(ema_20 - ema_50)
        if ema_spread < (0.05 * atr):
            return "tight_ema_spread"

        bullish_cross = (prev_ema20 <= prev_ema50) and (ema_20 > ema_50)
        bearish_cross = (prev_ema20 >= prev_ema50) and (ema_20 < ema_50)
        bullish_trend = (ema_20 > ema_50) and (close >= ema_20)
        bearish_trend = (ema_20 < ema_50) and (close <= ema_20)

        config = REGIME_CONFIG["TREND"]

        # LONG: confirmed bullish momentum, RSI corridor (45-70), no punitive funding headwind
        if (bullish_cross or bullish_trend) and (45.0 <= rsi <= 70.0) and (funding <= 0.0004):
            if self._get_active_module_count("A") >= config["max_pos"] or not self._check_portfolio_heat():
                return "blocked_risk"
            await self._switch_module(symbol, "A")
            await self._set_leverage(symbol, config["leverage"])
            # Pullback pricing anchor: enter near EMA20 or close
            entry_price = min(close, ema_20 * 1.001)
            qty = await self.calculate_risk_position_size(symbol, entry_price, atr)
            ok = await self.place_directional_order(symbol, "BUY", entry_price, qty, atr, module="A")
            return "entered_long" if ok else "order_rejected"

        # SHORT: confirmed bearish momentum, RSI corridor (30-55), no punitive funding headwind
        elif (bearish_cross or bearish_trend) and (30.0 <= rsi <= 55.0) and (funding >= -0.0004):
            if self._get_active_module_count("A") >= config["max_pos"] or not self._check_portfolio_heat():
                return "blocked_risk"
            await self._switch_module(symbol, "A")
            await self._set_leverage(symbol, config["leverage"])
            # Pullback pricing anchor: enter near EMA20 or close
            entry_price = max(close, ema_20 * 0.999)
            qty = await self.calculate_risk_position_size(symbol, entry_price, atr)
            ok = await self.place_directional_order(symbol, "SELL", entry_price, qty, atr, module="A")
            return "entered_short" if ok else "order_rejected"

        return "no_signal"

    async def execute_funding_module(self, symbol: str, mid_price: float):
        funding = self.funding_rates.get(symbol, 0.0)
        df = self.md_manager.get_dataframe(symbol)
        if len(df) < 14:
            return "warming_up"

        df = TechnicalIndicators.calculate_indicators(df)
        latest = df.iloc[-1]
        calc_atr = float(latest.get("atr", 0.0) or 0.0)
        atr = max(calc_atr, mid_price * MIN_ATR_RATIO)

        # Exit condition: harvest profit once funding normalizes
        if symbol in self.positions:
            pos = self.positions[symbol]
            if abs(funding) < FUNDING_EXIT_THRESHOLD:
                logger.info(f"[{symbol}] Module C funding normalized ({funding:.6f} < {FUNDING_EXIT_THRESHOLD:.6f}). Closing position...")
                closed = await self.close_position(symbol, reason=f"funding_normalized_{funding:.6f}")
                return "exit_placed" if closed else "order_rejected"
            return "already_positioned"

        config = REGIME_CONFIG["FUNDING"]
        if self._get_active_module_count("C") >= config["max_pos"] or not self._check_portfolio_heat():
            return "blocked_risk"

        collateral = await self.get_collateral_balance()
        current_notional = sum(abs(p.get("notional", 0.0)) for p in self.positions.values())
        remaining_heat = max(0.0, MAX_PORTFOLIO_NOTIONAL - current_notional)
        target_notional = min(TARGET_CARRY_NOTIONAL, MAX_SINGLE_TRADE_NOTIONAL, remaining_heat, collateral * 0.10)
        qty = target_notional / mid_price
        dec = DECIMALS_MAP.get(symbol)
        min_qty = 10 ** (-(dec.quantity_decimals if dec else 3))
        qty = max(min_qty, qty)

        # 1. Longs pay shorts (funding >= +10 bps/hr): enter SHORT carry position to harvest payments
        if funding >= FUNDING_SHORT_THRESHOLD:
            await self._switch_module(symbol, "C")
            await self._set_leverage(symbol, config["leverage"])
            entry_price = mid_price * (1.0 + MAKER_OFFSET_BPS)
            ok = await self.place_directional_order(symbol, "SELL", entry_price, qty, atr, module="C")
            logger.info(
                f"[{symbol}] Module C (Funding Carry) SHORT entered: funding={funding:.6f} "
                f"(+{funding*10000:.1f} bps/hr), notional=${target_notional:,.2f}"
            )
            return "entered_short" if ok else "order_rejected"

        # 2. Shorts pay longs (funding <= -10 bps/hr): enter LONG carry position to harvest payments
        elif funding <= FUNDING_LONG_THRESHOLD:
            await self._switch_module(symbol, "C")
            await self._set_leverage(symbol, config["leverage"])
            entry_price = mid_price * (1.0 - MAKER_OFFSET_BPS)
            ok = await self.place_directional_order(symbol, "BUY", entry_price, qty, atr, module="C")
            logger.info(
                f"[{symbol}] Module C (Funding Carry) LONG entered: funding={funding:.6f} "
                f"({funding*10000:.1f} bps/hr), notional=${target_notional:,.2f}"
            )
            return "entered_long" if ok else "order_rejected"

        return "no_signal"

    async def execute_breakout_module(self, symbol: str, mid_price: float):
        if symbol in self.positions:
            return "already_positioned"

        df = self.md_manager.get_dataframe(symbol)
        if len(df) < KELTNER_PERIOD:
            return "warming_up"

        df = TechnicalIndicators.calculate_indicators(df)
        latest = df.iloc[-1]

        oi_change = self._get_oi_change(symbol)
        avg_volume = self._get_avg_volume(symbol)
        current_volume = float(latest.get("volume", 0) or 0)
        if current_volume <= 0 and self.volume_history.get(symbol):
            current_volume = self.volume_history[symbol][-1][1]
        vol_confirmed = (current_volume > avg_volume * 1.5) if avg_volume > 0 else True
        calc_atr = float(latest.get("atr", 0.0) or 0.0)
        atr = max(calc_atr, mid_price * MIN_ATR_RATIO)

        config = REGIME_CONFIG["BREAKOUT"]

        if latest["close"] > latest["keltner_upper"] and oi_change > OI_SURGE_THRESHOLD and vol_confirmed:
            if self._get_active_module_count("D") >= config["max_pos"] or not self._check_portfolio_heat():
                return "blocked_risk"
            await self._switch_module(symbol, "D")
            await self._set_leverage(symbol, config["leverage"])
            qty = await self.calculate_risk_position_size(symbol, mid_price, atr)
            ok = await self.place_directional_order(symbol, "BUY", mid_price, qty, atr, module="D")
            logger.info(f"[{symbol}] Module D LONG: OI_chg={oi_change:.2%}")
            return "entered_long" if ok else "order_rejected"

        elif latest["close"] < latest["keltner_lower"] and oi_change > OI_SURGE_THRESHOLD and vol_confirmed:
            if self._get_active_module_count("D") >= config["max_pos"] or not self._check_portfolio_heat():
                return "blocked_risk"
            await self._switch_module(symbol, "D")
            await self._set_leverage(symbol, config["leverage"])
            qty = await self.calculate_risk_position_size(symbol, mid_price, atr)
            ok = await self.place_directional_order(symbol, "SELL", mid_price, qty, atr, module="D")
            logger.info(f"[{symbol}] Module D SHORT: OI_chg={oi_change:.2%}")
            return "entered_short" if ok else "order_rejected"

        return "no_signal"

    # -------------------------------------------------------------------------
    # Module E: Cointegrated Delta-Neutral Stat-Arb & MM Instant Round-Turn
    # -------------------------------------------------------------------------
    async def _handle_instant_round_turn(self, symbol: str, fill_side: str, fill_price: float, fill_qty: float):
        """Immediately place counter Maker limit order upon grid fill to complete the round-turn."""
        if fill_price <= 0 or fill_qty <= 0:
            return
        try:
            counter_side = "SELL" if "BUY" in fill_side.upper() else "BUY"
            spread_offset = MM_ROUND_TURN_SPREAD_BPS
            target_price = fill_price * (1.0 + spread_offset) if counter_side == "SELL" else fill_price * (1.0 - spread_offset)

            p_str = self.format_price(symbol, target_price, rounding=ROUND_UP if counter_side == "SELL" else ROUND_DOWN)
            q_str = self.format_qty(symbol, fill_qty)

            opts = PlaceOrderOptions(reduce_only=True, post_only=True, stp_mode="CANCEL_AGGRESSOR")
            logger.info(
                f"[{symbol}] [MM_ROUND_TURN] Posting counter Maker {counter_side} {q_str} @ {p_str} "
                f"(+{spread_offset*10000:.1f} bps above fill {fill_price:.6g}) to lock in spread..."
            )
            if DRY_RUN:
                logger.info(f"[{symbol}] [MM_ROUND_TURN] DRY_RUN enabled. Counter order simulated.")
                return

            ack = await self.client.place_order(
                symbol=symbol,
                side=to_sdk_side(counter_side),
                order_type=OrderType.LIMIT,
                quantity=q_str,
                price=p_str,
                time_in_force=TimeInForce.GTC,
                options=opts,
            )
            if getattr(ack, "success", True):
                logger.info(f"[{symbol}] [MM_ROUND_TURN] Counter order placed successfully.")
            else:
                logger.warning(f"[{symbol}] [MM_ROUND_TURN] Counter order placement notice: {getattr(ack, 'error', None)}")
        except Exception as e:
            logger.debug(f"[{symbol}] Round-turn order notice: {e}")

    async def evaluate_stat_arb_pair(self):
        """Delta-Neutral Statistical Arbitrage between ETH-USDC-PERP and BTC-USDC-PERP."""
        eth_sym = "ETH-USDC-PERP"
        btc_sym = "BTC-USDC-PERP"
        eth_mid = self.md_manager.current_mids.get(eth_sym, 0.0)
        btc_mid = self.md_manager.current_mids.get(btc_sym, 0.0)

        if eth_mid <= 0 or btc_mid <= 0:
            return

        ratio = eth_mid / btc_mid
        now_ts = time.time()
        self.eth_btc_ratios.append((now_ts, ratio))

        if len(self.eth_btc_ratios) < 20:
            return

        ratios = [r for _, r in self.eth_btc_ratios]
        mean_r = float(np.mean(ratios))
        std_r = float(np.std(ratios))
        if std_r <= 1e-8:
            return

        z_score = (ratio - mean_r) / std_r

        # Active Pair Management
        if self.stat_arb_active:
            # Mean Reversion Target
            if (self.stat_arb_active == "LONG_ETH_SHORT_BTC" and z_score >= -STAT_ARB_EXIT_Z) or \
               (self.stat_arb_active == "SHORT_ETH_LONG_BTC" and z_score <= STAT_ARB_EXIT_Z):
                logger.info(
                    f"[STAT_ARB] Mean-reversion target reached (z={z_score:.2f}, entry_z={self.stat_arb_entry_z:.2f}). "
                    f"Closing ETH/BTC pair..."
                )
                await self.close_position(eth_sym, reason=f"stat_arb_mean_revert_z_{z_score:.2f}")
                await self.close_position(btc_sym, reason=f"stat_arb_mean_revert_z_{z_score:.2f}")
                self.stat_arb_active = None
                return

            # Divergence Stop Loss
            if abs(z_score) >= STAT_ARB_STOP_Z:
                logger.warning(
                    f"[STAT_ARB] Divergence stop hit (z={z_score:.2f}). Closing pair to protect capital..."
                )
                await self.close_position(eth_sym, reason=f"stat_arb_stop_loss_z_{z_score:.2f}")
                await self.close_position(btc_sym, reason=f"stat_arb_stop_loss_z_{z_score:.2f}")
                self.stat_arb_active = None
                return

            return

        # New Pair Entry Evaluation
        if eth_sym in self.positions or btc_sym in self.positions:
            return
        if not self._check_portfolio_heat():
            return

        collateral = await self.get_collateral_balance()
        leg_notional = min(STAT_ARB_NOTIONAL, collateral * 0.05, MAX_SINGLE_TRADE_NOTIONAL / 2.0)
        eth_qty = leg_notional / eth_mid
        btc_qty = leg_notional / btc_mid

        # Case 1: ETH is underpriced relative to BTC (z <= -STAT_ARB_ENTRY_Z)
        if z_score <= -STAT_ARB_ENTRY_Z:
            logger.info(
                f"[STAT_ARB] Signal: LONG ETH / SHORT BTC (z={z_score:.2f}, ratio={ratio:.6f}, leg=${leg_notional:,.0f})"
            )
            await self._switch_module(eth_sym, "E")
            await self._switch_module(btc_sym, "E")
            await self._set_leverage(eth_sym, 2)
            await self._set_leverage(btc_sym, 2)

            ok_eth = await self.place_directional_order(eth_sym, "BUY", eth_mid, eth_qty, eth_mid * 0.005, module="E")
            ok_btc = await self.place_directional_order(btc_sym, "SELL", btc_mid, btc_qty, btc_mid * 0.005, module="E")

            if ok_eth and ok_btc:
                self.stat_arb_active = "LONG_ETH_SHORT_BTC"
                self.stat_arb_entry_z = z_score

        # Case 2: ETH is overpriced relative to BTC (z >= STAT_ARB_ENTRY_Z)
        elif z_score >= STAT_ARB_ENTRY_Z:
            logger.info(
                f"[STAT_ARB] Signal: SHORT ETH / LONG BTC (z={z_score:.2f}, ratio={ratio:.6f}, leg=${leg_notional:,.0f})"
            )
            await self._switch_module(eth_sym, "E")
            await self._switch_module(btc_sym, "E")
            await self._set_leverage(eth_sym, 2)
            await self._set_leverage(btc_sym, 2)

            ok_eth = await self.place_directional_order(eth_sym, "SELL", eth_mid, eth_qty, eth_mid * 0.005, module="E")
            ok_btc = await self.place_directional_order(btc_sym, "BUY", btc_mid, btc_qty, btc_mid * 0.005, module="E")

            if ok_eth and ok_btc:
                self.stat_arb_active = "SHORT_ETH_LONG_BTC"
                self.stat_arb_entry_z = z_score

    # -------------------------------------------------------------------------
    # Regime Switching & Feeders
    # -------------------------------------------------------------------------
    def _get_oi_change(self, symbol: str, hours: int = OI_LOOKBACK_HOURS) -> float:
        history = self.oi_history.get(symbol)
        if not history or len(history) < 2:
            return 0.0
        cutoff = time.time() - (hours * 3600)
        recent = [oi for ts, oi in history if ts >= cutoff]
        if len(recent) < 2 or recent[0] <= 0:
            return 0.0
        return (recent[-1] - recent[0]) / recent[0]

    def _get_avg_volume(self, symbol: str, hours: int = 1) -> float:
        history = self.volume_history.get(symbol)
        if not history:
            return 0.0
        cutoff = time.time() - (hours * 3600)
        recent = [vol for ts, vol in history if ts >= cutoff]
        return (sum(recent) / len(recent)) if recent else 0.0

    def _volume_surge(self, symbol: str, mult: float = 1.5) -> bool:
        history = self.volume_history.get(symbol)
        if not history or len(history) < 3:
            return False
        recent = [v for _, v in history]
        prior = recent[:-1]
        avg = sum(prior) / len(prior)
        return avg > 0 and recent[-1] > avg * mult

    def _detect_regime(self, symbol: str) -> str:
        df = self.md_manager.get_dataframe(symbol)
        if len(df) < 20:
            return "WARMUP"

        df = TechnicalIndicators.calculate_indicators(df)
        latest = df.iloc[-1]

        adx = float(latest.get("adx", 0.0) or 0.0)
        bb_mid = float(latest.get("bb_mid", 1.0) or 1.0)
        bb_upper = float(latest.get("bb_upper", 0.0) or 0.0)
        bb_lower = float(latest.get("bb_lower", 0.0) or 0.0)
        bb_width = (bb_upper - bb_lower) / bb_mid if bb_mid > 0 else 0.0

        funding = abs(self.funding_rates.get(symbol, 0.0))

        # Institutional Regime Priority:
        # 1. High Funding Yield (> 10 bps/hr): Prioritize Carry Harvesting (Risk-Free Cash Yield)
        if funding >= FUNDING_SHORT_THRESHOLD:
            raw_regime = "FUNDING"
        # 2. Cointegrated Delta-Neutral Stat-Arb pair active
        elif symbol in ("ETH-USDC-PERP", "BTC-USDC-PERP") and self.stat_arb_active:
            raw_regime = "STAT_ARB"
        # 3. Dense Market Making with Instant Round-Turn Spread Harvesting
        else:
            raw_regime = "MEAN_REV"

        # Apply regime stability filter to prevent rapid churn
        current_mod = self.active_modules.get(symbol)
        current_regime = None
        for r_name, r_cfg in REGIME_CONFIG.items():
            if r_cfg["module"] == current_mod:
                current_regime = r_name
                break

        if current_regime and raw_regime != current_regime:
            pending_regime, count = self.regime_pending.get(symbol, (raw_regime, 0))
            if pending_regime == raw_regime:
                count += 1
            else:
                pending_regime = raw_regime
                count = 1
            self.regime_pending[symbol] = (pending_regime, count)

            if count < REGIME_STABILITY_CYCLES:
                # Retain current regime until new regime signal persists across consecutive cycles
                return current_regime

        self.regime_pending[symbol] = (raw_regime, 0)
        return raw_regime

    async def _switch_module(self, symbol: str, new_module: str):
        old_module = self.active_modules.get(symbol)
        if old_module and old_module != new_module:
            logger.info(f"[{symbol}] Switching Module {old_module} -> {new_module}")
            if old_module == "B":
                await self.clear_grid_orders(symbol)

            regime = self._detect_regime(symbol)
            self.trade_logger.log_regime_switch(symbol, old_module, new_module, regime)
            if self.telegram.enabled:
                msg = self.telegram.format_regime_switch(symbol, old_module, new_module, regime)
                await self.telegram.send(msg)

        self.active_modules[symbol] = new_module

    async def feed_prices_from_reference(self):
        """Live mid prices from Hyperliquid (the venue has no public price feed).

        Falls back to the OI-implied price in feed_prices_from_rest until the
        first reference tick arrives for a symbol.
        """
        while self.running:
            try:
                async with httpx.AsyncClient(timeout=5.0) as http:
                    resp = await http.post(REFERENCE_MID_URL, json={"type": "allMids"})
                    resp.raise_for_status()
                    mids = resp.json()
                for symbol, key in REFERENCE_MID_KEYS.items():
                    price = float(mids.get(key, 0) or 0)
                    if price > 0:
                        self.md_manager.push_tick(symbol, price)
                        self._ref_fed.add(symbol)
            except Exception as e:
                logger.debug(f"Reference price feed notice: {e}")
            await asyncio.sleep(FEED_INTERVAL_SECONDS)

    async def feed_prices_from_rest(self):
        while self.running:
            try:
                oi_rows = await self.rest_client.get_open_interest()
                by_sid = {}
                for row in oi_rows or []:
                    sid = row.get("symbol_id") if isinstance(row, dict) else getattr(row, "symbol_id", None)
                    if sid in SYMBOL_IDS.values():
                        by_sid[sid] = row

                for symbol, sid in SYMBOL_IDS.items():
                    row = by_sid.get(sid)
                    if not row:
                        continue
                    oi = float(row.get("open_interest") if isinstance(row, dict) else getattr(row, "open_interest", 0) or 0)
                    ccy = float(row.get("oi_ccy") if isinstance(row, dict) else getattr(row, "oi_ccy", 0) or 0)
                    if oi > 0 and ccy > 0:
                        self.oi_history[symbol].append((time.time(), oi))
                        # OI-implied price lags badly; only use it until the
                        # reference feed has produced a real tick for this symbol.
                        if symbol not in self._ref_fed:
                            self.md_manager.push_tick(symbol, ccy / oi)
                    else:
                        mark = float(row.get("mark_price", 0) if isinstance(row, dict) else getattr(row, "mark_price", 0) or 0)
                        if mark > 0 and symbol not in self._ref_fed:
                            self.md_manager.push_tick(symbol, mark)
            except Exception as e:
                logger.debug(f"REST price feeder notice: {e}")
            await asyncio.sleep(FEED_INTERVAL_SECONDS)

    async def evaluate_symbol(self, symbol: str):
        df = self.md_manager.get_dataframe(symbol)
        if len(df) < 20:
            logger.info(f"[{symbol}] Warming up: {len(df)}/20 ticks recorded.")
            return

        regime = self._detect_regime(symbol)
        config = REGIME_CONFIG.get(regime)
        mid_price = self.md_manager.current_mids.get(symbol)

        if not config or regime == "WARMUP":
            logger.info(f"[{symbol}] regime={regime} decision=skip reason=unrouted")
            return
        if not mid_price or mid_price <= 0:
            logger.info(f"[{symbol}] regime={regime} decision=skip reason=no_mid_price")
            return

        # Active position management: if position is open, trail profits, hit TP, stop loss, or exit reversals
        if symbol in self.positions:
            mgmt_status = await self.manage_open_position(symbol, mid_price)
            logger.info(
                f"[{symbol}] regime={regime} active_pos_mgmt={mgmt_status} mid={mid_price:.6g}"
            )
            return

        module = config["module"]

        # Institutional frequency control: enforce 10-minute cooldown on directional entries to stop churn
        now_ts = time.time()
        time_since_trade = now_ts - self.last_trade_time.get(symbol, 0.0)
        if module in ("A", "C", "D") and time_since_trade < TRADE_COOLDOWN_SECONDS:
            rem_cd = int(TRADE_COOLDOWN_SECONDS - time_since_trade)
            logger.info(
                f"[{symbol}] regime={regime} module={module} mid={mid_price:.6g} "
                f"decision=skip reason=trade_cooldown ({rem_cd}s remaining)"
            )
            return

        if module == "A":
            result = await self.execute_trend_module(symbol, mid_price)
        elif module == "B":
            result = await self.execute_grid_module(symbol, mid_price)
        elif module == "C":
            result = await self.execute_funding_module(symbol, mid_price)
        elif module == "D":
            result = await self.execute_breakout_module(symbol, mid_price)
        else:
            result = "unrouted"
        logger.info(
            f"[{symbol}] regime={regime} module={module} mid={mid_price:.6g} "
            f"funding={self.funding_rates.get(symbol, 0.0):.6f} decision={result or 'no_signal'}"
        )

    # -------------------------------------------------------------------------
    # WebSocket Listeners
    # -------------------------------------------------------------------------
    async def listen_order_updates(self):
        last_sync = 0.0
        try:
            async for update in self.client.order_updates():
                logger.info(f"Order Update Stream: {update}")
                update_type = getattr(update, "update_type", None)
                status = getattr(update, "status", None)
                is_filled = (
                    "FILLED" in str(update_type).upper()
                    or "FILLED" in str(status).upper()
                )
                if is_filled:
                    sid = getattr(update, "symbol_id", None)
                    symbol = SYMBOL_BY_ID.get(sid, str(sid))
                    side = str(getattr(update, "side", ""))
                    price = float(getattr(update, "price", 0) or 0)
                    qty = float(getattr(update, "filled_qty", 0) or getattr(update, "quantity", 0) or 0)
                    pnl_raw = getattr(update, "realized_pnl", None)
                    realized_pnl = float(pnl_raw) if pnl_raw is not None else None
                    oid = getattr(update, "order_id", None)
                    is_post_only = bool(getattr(update, "post_only", False))
                    fee_rate = self.maker_fee_pct if is_post_only else self.taker_fee_pct
                    fee_est = price * qty * fee_rate if price and qty else 0.0

                    self.trade_logger.log_fill(
                        symbol=symbol,
                        side=side,
                        fill_price=price,
                        fill_qty=qty,
                        realized_pnl=realized_pnl,
                        trading_fee=fee_est,
                        order_id=str(oid) if oid else None,
                    )
                    if self.telegram.enabled:
                        msg = self.telegram.format_fill(
                            symbol=symbol,
                            side=side,
                            price=price,
                            qty=qty,
                            realized_pnl=realized_pnl,
                            fee=fee_est,
                            order_id=str(oid) if oid else None,
                        )
                        await self.telegram.send(msg)

                    # Trigger instant round-turn counter order if fill was for Market Making lot
                    mod = self.position_source_module.get(symbol, self.active_modules.get(symbol, "B"))
                    if mod == "B" and price > 0 and qty > 0:
                        asyncio.create_task(self._handle_instant_round_turn(symbol, side, price, qty))

                now = time.monotonic()
                if now - last_sync >= SYNC_THROTTLE_SECONDS:
                    last_sync = now
                    await self.sync_positions()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Order update consumer error: {e}")

    async def _seed_funding_rates(self):
        try:
            rows = await self.rest_client.get_funding_rates()
            seeded = 0
            for row in rows or []:
                sid = row.get("symbol_id") if isinstance(row, dict) else getattr(row, "symbol_id", None)
                symbol = SYMBOL_BY_ID.get(sid)
                rate = row.get("funding_rate") if isinstance(row, dict) else getattr(row, "funding_rate", None)
                if symbol and rate is not None:
                    self.funding_rates[symbol] = float(rate)
                    seeded += 1
            if seeded:
                logger.info(f"Seeded funding rates for {seeded} symbol(s): "
                            + ", ".join(f"{s}={r:.6f}" for s, r in self.funding_rates.items()))
        except Exception as e:
            logger.warning(f"Funding rate seed failed (WS pushes will fill in): {e}")

    async def setup_market_data_stream(self):
        for event_name, handler in [
            ("on_funding_rate_update", self._handle_funding_rate),
            ("on_open_interest_snapshot", self._handle_open_interest),
            ("on_volume_snapshot", self._handle_volume),
        ]:
            if hasattr(self.client, event_name):
                try:
                    getattr(self.client, event_name)(handler)
                except Exception as e:
                    logger.debug(f"Could not bind listener {event_name}: {e}")

        try:
            await self.client.subscribe(["funding_rate", "open_interest", "volume", "orders"])
            logger.info("Subscribed to WebSocket channels: funding_rate, open_interest, volume, orders")
        except Exception as e:
            logger.warning(f"Failed subscribing to WS channels: {e}")

    def _handle_funding_rate(self, update):
        symbol = self._resolve_event_symbol(update)
        rate = None
        if isinstance(update, dict):
            rate = update.get("funding_rate")
        else:
            rate = getattr(update, "funding_rate", None)
        if symbol and rate is not None:
            self.funding_rates[symbol] = float(rate)

    @staticmethod
    def _resolve_event_symbol(item) -> Optional[str]:
        symbol = item.get("symbol") if isinstance(item, dict) else getattr(item, "symbol", None)
        if symbol:
            return symbol
        sid = item.get("symbol_id") if isinstance(item, dict) else getattr(item, "symbol_id", None)
        return SYMBOL_BY_ID.get(sid)

    def _handle_open_interest(self, msg):
        items = msg.get("data", []) if isinstance(msg, dict) else getattr(msg, "data", [])
        for item in items:
            symbol = self._resolve_event_symbol(item)
            oi = float(item.get("open_interest", 0) if isinstance(item, dict) else getattr(item, "open_interest", 0) or 0)
            if symbol in self.oi_history and oi > 0:
                self.oi_history[symbol].append((time.time(), oi))

    def _handle_volume(self, msg):
        items = msg.get("data", []) if isinstance(msg, dict) else getattr(msg, "data", [])
        for item in items:
            symbol = self._resolve_event_symbol(item)
            vol = float(item.get("volume", 0) if isinstance(item, dict) else getattr(item, "volume", 0) or 0)
            if symbol in self.volume_history and vol > 0:
                self.volume_history[symbol].append((time.time(), vol))

    async def _fetch_tier_status(self):
        """Query account VIP tier status to set authoritative maker/taker fee rates."""
        try:
            token = getattr(self.rest_client, "bearer_token", None)
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            async with httpx.AsyncClient(timeout=5.0) as http:
                resp = await http.get(f"{REST_BASE_URL}/api/v1/tiers/status", headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    cur = data.get("current_config", {})
                    m_bps = float(cur.get("maker_fee_bps", 1.5))
                    t_bps = float(cur.get("taker_fee_bps", 5.5))
                    self.maker_fee_pct = m_bps / 10000.0
                    self.taker_fee_pct = t_bps / 10000.0
                    t_name = data.get("tier_name", "Core")
                    logger.info(
                        f"Verified VIP Tier '{t_name}': Maker={self.maker_fee_pct*100:.3f}% ({m_bps} bps), "
                        f"Taker={self.taker_fee_pct*100:.3f}% ({t_bps} bps)"
                    )
                    return
                # Fallback to public tier config if user tier status endpoint is unauthenticated
                resp_cfg = await http.get(f"{REST_BASE_URL}/api/v1/tiers/config")
                if resp_cfg.status_code == 200:
                    cfg_list = resp_cfg.json()
                    if isinstance(cfg_list, list) and cfg_list:
                        cur = cfg_list[0]
                        m_bps = float(cur.get("maker_fee_bps", 1.5))
                        t_bps = float(cur.get("taker_fee_bps", 5.5))
                        self.maker_fee_pct = m_bps / 10000.0
                        self.taker_fee_pct = t_bps / 10000.0
                        logger.info(
                            f"Loaded default VIP Tier schedule: Maker={self.maker_fee_pct*100:.3f}% ({m_bps} bps), "
                            f"Taker={self.taker_fee_pct*100:.3f}% ({t_bps} bps)"
                        )
        except Exception as e:
            logger.debug(f"Fee tier inquiry notice (retaining Core defaults {self.maker_fee_pct*100:.3f}%/{self.taker_fee_pct*100:.3f}%): {e}")

    async def run_trading_loop(self):
        swept = await self._startup_order_sweep()

        logger.info("Initializing leverage levels across active symbols...")
        for symbol in SYMBOLS:
            await self._set_leverage(symbol, DEFAULT_LEVERAGE)

        await self._fetch_tier_status()
        await self.sync_positions()
        await self._seed_funding_rates()
        asyncio.create_task(self.listen_order_updates())
        asyncio.create_task(self.feed_prices_from_rest())
        asyncio.create_task(self.feed_prices_from_reference())
        await self.setup_market_data_stream()

        logger.info("Agent trading loop activated.")
        await self._send_startup_alert(swept)
        try:
            while self.running:
                try:
                    await self.sync_positions()
                except Exception as e:
                    logger.debug(f"Position sync notice: {e}")

                if self.system_health_accepting:
                    # 1. Delta-Neutral Cointegrated Stat-Arb Pair (ETH/BTC)
                    try:
                        await self.evaluate_stat_arb_pair()
                    except Exception as e:
                        logger.error(f"Stat-Arb Execution Error: {e}", exc_info=True)

                    # 2. Individual Symbol Execution (Funding Carry & Dense MM)
                    for symbol in SYMBOLS:
                        try:
                            await self.evaluate_symbol(symbol)
                        except Exception as e:
                            logger.error(f"[{symbol}] Execution Error: {e}", exc_info=True)

                await asyncio.sleep(LOOP_INTERVAL_SECONDS)
        finally:
            try:
                await self.shutdown()
            except (Exception, asyncio.CancelledError):
                pass

    async def shutdown(self):
        logger.info("Initiating agent shutdown...")
        self.running = False
        for symbol in SYMBOLS:
            await self.clear_grid_orders(symbol)
        try:
            ack = await self.client.cancel_all_orders()
            count = getattr(ack, "count", 0) or 0
            if count:
                logger.info(f"Shutdown sweep: cancelled {count} resting order(s).")
        except Exception as e:
            logger.warning(f"Shutdown order sweep failed: {e}")
        if self.md_client:
            await self.md_client.disconnect()


# -----------------------------------------------------------------------------
# Main Runtime Loop
# -----------------------------------------------------------------------------
async def main():
    backoff = 2
    max_backoff = 60
    failure_alert_sent = False
    last_error = ""

    while True:
        try:
            logger.info("Connecting to GoDark Private WS and REST Endpoints...")
            async with GodarkClient(
                api_key_id=API_KEY_ID,
                api_secret=API_SECRET,
                passphrase=PASSPHRASE,
                base_url=WS_URL,
                transport=transport_config,
                stream_buffer_size=2048,
            ) as client:
                async with GodarkRestClient(
                    api_key_id=API_KEY_ID,
                    api_secret=API_SECRET,
                    passphrase=PASSPHRASE,
                    rest_base_url=REST_BASE_URL,
                ) as rest_client:
                    agent = QuantitativeTradingAgent(client, rest_client)
                    backoff = 2
                    failure_alert_sent = False
                    await agent.run_trading_loop()

        except AuthenticationError as e:
            logger.critical(f"FATAL AUTHENTICATION FAILURE: {e}. Exiting.")
            if telegram_notifier.enabled:
                await telegram_notifier.send(
                    f"🚨 <b>Quant Agent FATAL</b>\nAuthentication failed: {e}\nAgent exiting."
                )
            break
        except SessionError as e:
            last_error = f"HPKE session error: {e}"
            logger.warning(f"HPKE Session Error: {e}. Re-authenticating in {backoff}s...")
        except (GDXConnectionError, GDXTimeoutError, OSError) as e:
            last_error = f"network error: {e}"
            logger.warning(f"Network Connection Lost: {e}. Retrying in {backoff}s...")
        except GodarkError as e:
            last_error = f"SDK error: {e}"
            logger.error(f"SDK Error: {e}. Retrying in {backoff}s...")
        except Exception as e:
            last_error = f"unhandled error: {e}"
            logger.error(f"Unhandled Agent Exception: {e}. Retrying in {backoff}s...", exc_info=True)

        if backoff >= max_backoff and not failure_alert_sent and telegram_notifier.enabled:
            failure_alert_sent = True
            await telegram_notifier.send(
                f"⚠️ <b>Quant Agent problem</b>\nRepeated failures, retrying every {max_backoff}s.\n"
                f"<b>Last error:</b> {last_error}\n"
                f"<b>Time:</b> {datetime.utcnow().strftime('%H:%M:%S UTC')}"
            )

        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, max_backoff)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Agent process terminated manually.")   