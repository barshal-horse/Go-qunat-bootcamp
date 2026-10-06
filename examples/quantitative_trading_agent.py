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
from decimal import Decimal, ROUND_DOWN, ROUND_UP, ROUND_HALF_UP
from typing import Dict, List, Optional, Tuple, Set, Deque
from datetime import datetime

import pandas as pd

# --- Telegram Notifier ---
class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.enabled = bool(bot_token and chat_id)
    
    async def send(self, text: str) -> bool:
        if not self.enabled:
            return False
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/sendMessage",
                    json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"}
                ) as resp:
                    return resp.status == 200
        except Exception as e:
            logging.warning(f"Telegram notification failed: {e}")
            return False
    
    def format_trade(self, symbol: str, side: str, price: float, qty: float, 
                     module: str, order_id: str = None, 
                     sl: float = None, tp: float = None) -> str:
        emoji = "🟢" if side == "BUY" else "🔴"
        mod_name = {"A": "Trend", "B": "Grid", "C": "Funding", "D": "Breakout"}.get(module, module)
        lines = [
            f"{emoji} <b>Trade Executed</b> {emoji}",
            f"<b>Symbol:</b> {symbol}",
            f"<b>Side:</b> {side}",
            f"<b>Price:</b> {price:,.8f}",
            f"<b>Qty:</b> {qty}",
            f"<b>Module:</b> {mod_name} ({module})",
        ]
        if sl:
            lines.append(f"<b>SL:</b> {sl:,.8f}")
        if tp:
            lines.append(f"<b>TP:</b> {tp:,.8f}")
        if order_id:
            lines.append(f"<b>Order ID:</b> <code>{order_id}</code>")
        lines.append(f"<b>Time:</b> {datetime.utcnow().strftime('%H:%M:%S UTC')}")
        return "\n".join(lines)
    
    def format_grid(self, symbol: str, bid_price: float, ask_price: float, 
                    qty: float, order_ids: list) -> str:
        lines = [
            f"📊 <b>Grid Placed</b> 📊",
            f"<b>Symbol:</b> {symbol}",
            f"<b>Bid:</b> {bid_price:,.8f} | <b>Ask:</b> {ask_price:,.8f}",
            f"<b>Qty per leg:</b> {qty}",
            f"<b>Orders:</b> {len(order_ids)} placed",
            f"<b>Time:</b> {datetime.utcnow().strftime('%H:%M:%S UTC')}",
        ]
        return "\n".join(lines)
    
    def format_regime_switch(self, symbol: str, old_module: str, new_module: str, 
                              regime: str) -> str:
        mod_name = {"A": "Trend", "B": "Grid", "C": "Funding", "D": "Breakout"}
        old_name = mod_name.get(old_module, old_module)
        new_name = mod_name.get(new_module, new_module)
        return (
            f"🔄 <b>Regime Switch</b>\n"
            f"<b>Symbol:</b> {symbol}\n"
            f"<b>Regime:</b> {regime}\n"
            f"<b>Module:</b> {old_name} → {new_name}"
        )

# Ensure local vendored godark package is importable
try:
    from godark import GodarkClient, MarketDataClient, GodarkRestClient, PlaceOrderOptions
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
    print(
        "ERROR: Could not import 'godark'. Ensure script is executed inside the "
        "repo environment with vendored SDK path.",
        file=sys.stderr,
    )
    sys.exit(1)

# Import local helpers
try:
    from trade_safety import post_only_price
except ImportError:
    post_only_price = None

# -----------------------------------------------------------------------------
# Configuration & Setup
# -----------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv  # repo-local stdlib .env loader (examples/dotenv.py)
load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
load_dotenv()
os.environ["GODARK_ENABLE_TRADES"] = "0"
from godark import TransportConfig

# Telegram notifier (uses env vars)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
telegram_notifier = TelegramNotifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)

# Configure WebSocket transport for better resilience to server ping issues
transport_config = TransportConfig(
    heartbeat_interval=20.0,       # ping every 20s (server idle timeout is 30s)
    stale_timeout=900.0,           # wait 15min before stale (was 2min)
    missed_heartbeat_limit=10,     # allow 10 missed pings (was 2)
    command_timeout=60.0,          # increase command timeout for slow server responses
)
logger = logging.getLogger("GoDarkQuantAgent")

API_KEY_ID = os.getenv("GODARK_API_KEY_ID")
API_SECRET = os.getenv("GODARK_API_SECRET")
PASSPHRASE = os.getenv("GODARK_PASSPHRASE")
WS_URL = os.getenv("GODARK_WS_URL", "wss://api.godark-dex.com")
DRY_RUN = os.getenv("DRY_RUN", "false").lower() in ("true", "1", "yes")

if not all([API_KEY_ID, API_SECRET, PASSPHRASE]):
    logger.critical("Missing required env variables: GODARK_API_KEY_ID, GODARK_API_SECRET, or GODARK_PASSPHRASE")
    sys.exit(1)

# Derive REST HTTPS URL from WSS URL
REST_BASE_URL = WS_URL.replace("wss://", "https://").replace("ws://", "http://")

SYMBOLS = ["BTC-USDC-PERP", "ETH-USDC-PERP", "SOL-USDC-PERP"]
DEFAULT_LEVERAGE = 3
RISK_FACTOR = 0.015  # 1.5% portfolio risk per trade
LOOP_INTERVAL_SECONDS = 15
FEED_INTERVAL_SECONDS = 2  # price sampling cadence for the REST implied-mark feeder
MIN_NOTIONAL_USD = 100.0   # venue rejects orders below min notional (code 2013); use a safe floor
DECIMALS_MAP = load_offline_decimals_map()
SYMBOL_IDS = {"BTC-USDC-PERP": 1, "ETH-USDC-PERP": 2, "SOL-USDC-PERP": 5}  # offline fallback ids

# -----------------------------------------------------------------------------
# Strategy Parameters
# -----------------------------------------------------------------------------
# Module C: Funding Momentum
FUNDING_LONG_THRESHOLD = -0.0005   # -0.05% 8hr (shorts pay longs -> go LONG)
FUNDING_SHORT_THRESHOLD = 0.0005   # +0.05% 8hr (longs pay shorts -> go SHORT)
FUNDING_EXIT_THRESHOLD = 0.0001    # 0.01% 8hr (exit when funding normalizes)
FUNDING_MAX_HOLD_HOURS = 24

# Module D: OI-Weighted Breakout (Keltner)
OI_LOOKBACK_HOURS = 4
OI_SURGE_THRESHOLD = 0.20
OI_DROP_THRESHOLD = -0.15

KELTNER_PERIOD = 20
KELTNER_ATR_MULT = 2.0

# Regime Configuration
REGIME_CONFIG = {
    "TREND":       {"module": "A", "weight": 0.45, "leverage": 3, "max_pos": 2},
    "MEAN_REV":    {"module": "B", "weight": 0.25, "leverage": 2, "max_pos": 3},
    "FUNDING":     {"module": "C", "weight": 0.15, "leverage": 2, "max_pos": 2},
    "BREAKOUT":    {"module": "D", "weight": 0.15, "leverage": 3, "max_pos": 1},
}

# Portfolio heat limit
MAX_PORTFOLIO_HEAT = 0.80  # 80% max collateral utilization


# -----------------------------------------------------------------------------
# Technical Analysis Engine
# -----------------------------------------------------------------------------
class TechnicalIndicators:
    """Computes technical indicator series for Module A (Trend) and Module B (Grid/Mean-Reversion)."""

    @staticmethod
    def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
        if len(df) < 20:
            return df

        # EMAs
        df["ema_20"] = df["close"].ewm(span=20, adjust=False).mean()
        df["ema_50"] = df["close"].ewm(span=50, adjust=False).mean()

        # RSI 14
        delta = df["close"].diff()
        gain = delta.clip(lower=0).rolling(window=14).mean()
        loss = (-delta.clip(upper=0)).rolling(window=14).mean()
        rs = gain / (loss.replace(0, 1e-9))
        df["rsi"] = 100.0 - (100.0 / (1.0 + rs))

        # ATR 14
        high_low = df["high"] - df["low"]
        high_close = (df["high"] - df["close"].shift()).abs()
        low_close = (df["low"] - df["close"].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df["atr"] = tr.rolling(window=14).mean()

        # ADX 14
        up = df["high"].diff()
        down = -df["low"].diff()
        plus_dm = up.where((up > down) & (up > 0), 0.0)
        minus_dm = down.where((down > up) & (down > 0), 0.0)

        tr_smooth = tr.rolling(14).sum()
        plus_di = 100 * (plus_dm.rolling(14).sum() / tr_smooth.replace(0, 1e-9))
        minus_di = 100 * (minus_dm.rolling(14).sum() / tr_smooth.replace(0, 1e-9))
        dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-9))
        df["adx"] = dx.rolling(14).mean()

        # Bollinger Bands (20, 2)
        df["bb_mid"] = df["close"].rolling(20).mean()
        std = df["close"].rolling(20).std()
        df["bb_upper"] = df["bb_mid"] + (2.0 * std)
        df["bb_lower"] = df["bb_mid"] - (2.0 * std)

        # Keltner Channels (20, 2x ATR)
        df["keltner_mid"] = df["close"].ewm(span=KELTNER_PERIOD, adjust=False).mean()
        df["keltner_upper"] = df["keltner_mid"] + (KELTNER_ATR_MULT * df["atr"])
        df["keltner_lower"] = df["keltner_mid"] - (KELTNER_ATR_MULT * df["atr"])

        return df


# -----------------------------------------------------------------------------
# Market Data Feed Manager
# -----------------------------------------------------------------------------
class MarketDataManager:
    """Maintains real-time tick/orderbook data stream via MarketDataClient."""

    def __init__(self, symbols: List[str]):
        self.symbols = symbols
        self.price_history: Dict[str, List[Dict[str, float]]] = {s: [] for s in symbols}
        self.current_mids: Dict[str, float] = {}

    def push_tick(self, symbol: str, price: float, high: Optional[float] = None, low: Optional[float] = None, volume: Optional[float] = None):
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
        if len(self.price_history[symbol]) > 300:
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

        self.positions: Dict[str, Dict] = {}  # symbol -> position info
        self.open_grid_orders: Dict[str, List[int]] = {s: [] for s in SYMBOLS}
        self.system_health_accepting: bool = True
        self.running: bool = True

        # Multi-strategy state
        self.active_modules: Dict[str, str] = {}  # symbol -> active module
        self.funding_rates: Dict[str, float] = {}
        self.oi_history: Dict[str, Deque[Tuple[float, float]]] = {s: deque(maxlen=200) for s in SYMBOLS}  # (timestamp, oi)
        self.volume_history: Dict[str, Deque[Tuple[float, float]]] = {s: deque(maxlen=200) for s in SYMBOLS}  # (timestamp, volume)

        # Telegram notifier
        self.telegram = telegram_notifier

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
    # Account & Position Risk Management
    # -------------------------------------------------------------------------
    async def get_collateral_balance(self) -> float:
        try:
            acct = await self.rest_client.get_account()
            if acct and hasattr(acct, "summary"):
                return float(acct.summary.total_collateral or acct.summary.free_collateral or 10000.0)
        except Exception as e:
            logger.warning(f"Could not fetch account collateral via REST: {e}")
        return 10000.0  # Safe fallback estimate

    async def calculate_risk_position_size(self, symbol: str, entry_price: float, atr: float) -> float:
        collateral = await self.get_collateral_balance()
        risk_budget = collateral * RISK_FACTOR
        sl_distance = 1.5 * atr

        if sl_distance <= 0:
            return 0.0

        raw_qty = risk_budget / sl_distance
        max_notional = collateral * DEFAULT_LEVERAGE
        max_qty = max_notional / entry_price
        min_notional_qty = MIN_NOTIONAL_USD / entry_price

        final_qty = min(max(raw_qty, min_notional_qty), max_qty)
        dec = DECIMALS_MAP.get(symbol)
        min_qty = 10 ** (-(dec.quantity_decimals if dec else 3))

        return max(final_qty, min_qty)

    # -------------------------------------------------------------------------
    # Execution Engine
    # -------------------------------------------------------------------------
    async def place_directional_order(
        self, symbol: str, side: str, price: float, qty: float, atr: float,
        module: str = "A"
    ):
        p_str = self.format_price(symbol, price)
        q_str = self.format_qty(symbol, qty)

        sl = price - (1.5 * atr) if side == "BUY" else price + (1.5 * atr)
        tp = price + (3.0 * atr) if side == "BUY" else price - (3.0 * atr)

        opts = PlaceOrderOptions(
            stop_loss_price=self.format_price(symbol, sl),
            take_profit_price=self.format_price(symbol, tp),
            stp_mode="CANCEL_AGGRESSOR",
        )

        logger.info(
            f"[{symbol}] MODULE A Signal -> Side: {side} | Price: {p_str} | Qty: {q_str} | "
            f"SL: {opts.stop_loss_price} | TP: {opts.take_profit_price}"
        )

        if DRY_RUN:
            logger.info(f"[{symbol}] DRY_RUN enabled. Order execution skipped.")
            return

        try:
            res = await self.client.place_order(
                symbol=symbol,
                side=side,
                order_type="LIMIT",
                quantity=q_str,
                price=p_str,
                time_in_force="GTC",
                options=opts,
            )
            logger.info(f"[{symbol}] Directional order placed successfully: {res}")
            # Send Telegram notification
            order_id = getattr(res, 'order_id', None)
            if self.telegram.enabled:
                msg = self.telegram.format_trade(
                    symbol=symbol, side=side, price=price, qty=qty,
                    module=module, order_id=order_id,
                    sl=float(opts.stop_loss_price), tp=float(opts.take_profit_price)
                )
                await self.telegram.send(msg)
        except OrderError as e:
            logger.error(f"[{symbol}] Order Error (Code: {getattr(e, 'error_code', 'N/A')}): {e}")

    async def clear_grid_orders(self, symbol: str):
        order_ids = self.open_grid_orders.get(symbol, [])
        if not order_ids:
            return

        logger.info(f"[{symbol}] Clearing {len(order_ids)} stale Module B grid orders...")
        if not DRY_RUN:
            try:
                await self.client.batch_cancel(symbol, order_ids[:20])
            except Exception as e:
                logger.error(f"[{symbol}] Error clearing grid orders: {e}")
        self.open_grid_orders[symbol] = []

    async def execute_grid_module(self, symbol: str, mid_price: float):
        await self.clear_grid_orders(symbol)
        dec = DECIMALS_MAP.get(symbol)
        min_qty = 10 ** (-(dec.quantity_decimals if dec else 3))
        # Ensure each grid leg clears the venue minimum notional (BELOW_MIN_NOTIONAL = 2013)
        qty = max(min_qty, MIN_NOTIONAL_USD / mid_price)
        q_str = self.format_qty(symbol, qty)

        new_order_ids = []
        # Create 2 bid and 2 ask levels spaced at 0.2% and 0.4%
        offsets = [0.002, 0.004]

        for offset in offsets:
            bid_price = mid_price * (1.0 - offset)
            ask_price = mid_price * (1.0 + offset)

            bid_str = self.format_price(symbol, bid_price, rounding=ROUND_DOWN)
            ask_str = self.format_price(symbol, ask_price, rounding=ROUND_UP)

            opts = PlaceOrderOptions(post_only=True, stp_mode="CANCEL_AGGRESSOR")

            if not DRY_RUN:
                try:
                    b_res = await self.client.place_order(
                        symbol=symbol, side="BUY", order_type="LIMIT",
                        quantity=q_str, price=bid_str, time_in_force="GTC", options=opts,
                        confirmation="ack"
                    )
                    if hasattr(b_res, "order_id"):
                        new_order_ids.append(b_res.order_id)

                    a_res = await self.client.place_order(
                        symbol=symbol, side="SELL", order_type="LIMIT",
                        quantity=q_str, price=ask_str, time_in_force="GTC", options=opts,
                        confirmation="ack"
                    )
                    if hasattr(a_res, "order_id"):
                        new_order_ids.append(a_res.order_id)
                except OrderError as e:
                    logger.error(f"[{symbol}] Grid Placement Error ({getattr(e, 'error_code', 'N/A')}): {e}")

        self.open_grid_orders[symbol] = new_order_ids

    # -------------------------------------------------------------------------
    # Strategy Helpers
    # -------------------------------------------------------------------------
    def _get_oi_change(self, symbol: str, hours: int = OI_LOOKBACK_HOURS) -> float:
        """Calculate OI change over lookback period."""
        history = self.oi_history.get(symbol)
        if not history or len(history) < 2:
            return 0.0
        cutoff = time.time() - hours * 3600
        recent = [oi for ts, oi in history if ts >= cutoff]
        if len(recent) < 2:
            return 0.0
        return (recent[-1] - recent[0]) / recent[0] if recent[0] > 0 else 0.0

    def _get_avg_volume(self, symbol: str, hours: int = 1) -> float:
        """Get average volume over lookback period."""
        history = self.volume_history.get(symbol)
        if not history or len(history) < 2:
            return 0.0
        cutoff = time.time() - hours * 3600
        recent = [vol for ts, vol in history if ts >= cutoff]
        if not recent:
            return 0.0
        return sum(recent) / len(recent)

    def _detect_regime(self, symbol: str) -> str:
        """Detect market regime for a symbol."""
        df = self.md_manager.get_dataframe(symbol)
        if len(df) < 20:
            return "WARMUP"

        df = TechnicalIndicators.calculate_indicators(df)
        latest = df.iloc[-1]

        adx = latest.get("adx", 0)
        bb_mid = latest.get("bb_mid", 1)
        bb_upper = latest.get("bb_upper", 0)
        bb_lower = latest.get("bb_lower", 0)
        bb_width = (bb_upper - bb_lower) / bb_mid if bb_mid > 0 else 0
        funding = abs(self.funding_rates.get(symbol, 0))
        oi_change = self._get_oi_change(symbol)

        if funding > FUNDING_EXIT_THRESHOLD:
            return "FUNDING"
        elif adx > 25:
            return "TREND"
        elif bb_width < 0.015:
            return "BREAKOUT"
        else:
            return "MEAN_REV"

    def _check_portfolio_heat(self) -> bool:
        """Check if portfolio heat is within limits."""
        # This is a synchronous check; we use a cached collateral estimate
        # In production, you'd want to fetch fresh collateral
        try:
            # Quick estimate using position notional vs max leverage
            total_notional = sum(
                abs(pos.get("notional", 0)) for pos in self.positions.values()
            )
            # Assume 10k collateral * 3x leverage = 30k max notional
            max_notional = 10000.0 * 3.0
            heat = total_notional / max_notional if max_notional > 0 else 0
            return heat < MAX_PORTFOLIO_HEAT
        except Exception:
            return True

    def _get_active_module_count(self, module: str) -> int:
        """Count active positions for a module."""
        return sum(1 for m in self.active_modules.values() if m == module)

    async def _switch_module(self, symbol: str, new_module: str):
        """Switch active module for a symbol, clearing old orders."""
        old_module = self.active_modules.get(symbol)
        if old_module and old_module != new_module:
            logger.info(f"[{symbol}] Switching from Module {old_module} to Module {new_module}")
            if old_module == "B":
                await self.clear_grid_orders(symbol)
            # For directional modules (A, C, D), positions remain but we stop managing them
        self.active_modules[symbol] = new_module

    # -------------------------------------------------------------------------
    # Module C: Funding Momentum
    # -------------------------------------------------------------------------
    async def execute_funding_module(self, symbol: str, mid_price: float):
        funding = self.funding_rates.get(symbol, 0)
        df = self.md_manager.get_dataframe(symbol)
        if len(df) < 14:
            return

        df = TechnicalIndicators.calculate_indicators(df)
        latest = df.iloc[-1]
        rsi = latest["rsi"]
        atr = latest["atr"]

        # LONG: negative funding (shorts pay longs) + RSI not overbought
        if funding <= FUNDING_LONG_THRESHOLD and rsi < 70:
            await self._switch_module(symbol, "C")
            config = REGIME_CONFIG["FUNDING"]
            if self._get_active_module_count("C") >= config["max_pos"]:
                return
            if not self._check_portfolio_heat():
                return

            await self.client.update_leverage(symbol, config["leverage"])
            qty = await self.calculate_risk_position_size(symbol, mid_price, atr)
            await self.place_directional_order(symbol, "BUY", mid_price, qty, atr, module="C")
            logger.info(f"[{symbol}] Module C (Funding) LONG: funding={funding:.6f}, RSI={rsi:.1f}")

        # SHORT: positive funding (longs pay shorts) + RSI not oversold
        elif funding >= FUNDING_SHORT_THRESHOLD and rsi > 30:
            await self._switch_module(symbol, "C")
            config = REGIME_CONFIG["FUNDING"]
            if self._get_active_module_count("C") >= config["max_pos"]:
                return
            if not self._check_portfolio_heat():
                return

            await self.client.update_leverage(symbol, config["leverage"])
            qty = await self.calculate_risk_position_size(symbol, mid_price, atr)
            await self.place_directional_order(symbol, "SELL", mid_price, qty, atr, module="C")
            logger.info(f"[{symbol}] Module C (Funding) SHORT: funding={funding:.6f}, RSI={rsi:.1f}")

    # -------------------------------------------------------------------------
    # Module D: OI-Weighted Breakout (Keltner)
    # -------------------------------------------------------------------------
    async def execute_breakout_module(self, symbol: str, mid_price: float):
        df = self.md_manager.get_dataframe(symbol)
        if len(df) < KELTNER_PERIOD:
            return

        df = TechnicalIndicators.calculate_indicators(df)
        latest = df.iloc[-1]

        oi_change = self._get_oi_change(symbol)
        avg_volume = self._get_avg_volume(symbol)
        current_volume = latest.get("volume", 0)

        # LONG: breakout upper + OI surge + volume confirmation
        if (latest["close"] > latest["keltner_upper"] and
            oi_change > OI_SURGE_THRESHOLD and
            current_volume > avg_volume * 1.5 if avg_volume > 0 else False):

            await self._switch_module(symbol, "D")
            config = REGIME_CONFIG["BREAKOUT"]
            if self._get_active_module_count("D") >= config["max_pos"]:
                return
            if not self._check_portfolio_heat():
                return

            await self.client.update_leverage(symbol, config["leverage"])
            qty = await self.calculate_risk_position_size(symbol, mid_price, latest["atr"])
            await self.place_directional_order(symbol, "BUY", mid_price, qty, latest["atr"], module="D")
            logger.info(f"[{symbol}] Module D (Breakout) LONG: OI_chg={oi_change:.2%}, vol_ratio={current_volume/avg_volume:.2f}")

        # SHORT: breakout lower + OI surge + volume confirmation
        elif (latest["close"] < latest["keltner_lower"] and
              oi_change > OI_SURGE_THRESHOLD and
              current_volume > avg_volume * 1.5 if avg_volume > 0 else False):

            await self._switch_module(symbol, "D")
            config = REGIME_CONFIG["BREAKOUT"]
            if self._get_active_module_count("D") >= config["max_pos"]:
                return
            if not self._check_portfolio_heat():
                return

            await self.client.update_leverage(symbol, config["leverage"])
            qty = await self.calculate_risk_position_size(symbol, mid_price, latest["atr"])
            await self.place_directional_order(symbol, "SELL", mid_price, qty, latest["atr"], module="D")
            logger.info(f"[{symbol}] Module D (Breakout) SHORT: OI_chg={oi_change:.2%}, vol_ratio={current_volume/avg_volume:.2f}")

    # -------------------------------------------------------------------------
    # Module A: Trend (extracted from evaluate_symbol)
    # -------------------------------------------------------------------------
    async def execute_trend_module(self, symbol: str, mid_price: float):
        df = self.md_manager.get_dataframe(symbol)
        if len(df) < 20:
            return

        df = TechnicalIndicators.calculate_indicators(df)
        latest = df.iloc[-1]
        prev = df.iloc[-2]

        close = latest["close"]
        ema_20, ema_50 = latest["ema_20"], latest["ema_50"]
        prev_ema20, prev_ema50 = prev["ema_20"], prev["ema_50"]
        rsi, atr, adx = latest["rsi"], latest["atr"], latest["adx"]
        bb_upper, bb_lower = latest["bb_upper"], latest["bb_lower"]

        bullish_cross = (prev_ema20 <= prev_ema50) and (ema_20 > ema_50)
        bearish_cross = (prev_ema20 >= prev_ema50) and (ema_20 < ema_50)

        if bullish_cross and (50.0 <= rsi <= 65.0):
            await self._switch_module(symbol, "A")
            config = REGIME_CONFIG["TREND"]
            if self._get_active_module_count("A") >= config["max_pos"]:
                return
            if not self._check_portfolio_heat():
                return

            await self.client.update_leverage(symbol, config["leverage"])
            qty = await self.calculate_risk_position_size(symbol, close, atr)
            await self.place_directional_order(symbol, "BUY", close, qty, atr)

        elif bearish_cross and (35.0 <= rsi <= 50.0):
            await self._switch_module(symbol, "A")
            config = REGIME_CONFIG["TREND"]
            if self._get_active_module_count("A") >= config["max_pos"]:
                return
            if not self._check_portfolio_heat():
                return

            await self.client.update_leverage(symbol, config["leverage"])
            qty = await self.calculate_risk_position_size(symbol, close, atr)
            await self.place_directional_order(symbol, "SELL", close, qty, atr)

    # -------------------------------------------------------------------------
    # Module B: Grid/Mean Reversion (renamed from execute_grid_module)
    # -------------------------------------------------------------------------
    async def execute_grid_module(self, symbol: str, mid_price: float):
        await self._switch_module(symbol, "B")
        config = REGIME_CONFIG["MEAN_REV"]
        if self._get_active_module_count("B") >= config["max_pos"]:
            return

        await self.client.update_leverage(symbol, config["leverage"])
        # Call the original grid logic
        await self._execute_grid_logic(symbol, mid_price)

    async def _execute_grid_logic(self, symbol: str, mid_price: float):
        """Original grid execution logic."""
        await self.clear_grid_orders(symbol)
        dec = DECIMALS_MAP.get(symbol)
        min_qty = 10 ** (-(dec.quantity_decimals if dec else 3))
        qty = max(min_qty, MIN_NOTIONAL_USD / mid_price)
        q_str = self.format_qty(symbol, qty)

        new_order_ids = []
        offsets = [0.002, 0.004]

        for offset in offsets:
            bid_price = mid_price * (1.0 - offset)
            ask_price = mid_price * (1.0 + offset)

            bid_str = self.format_price(symbol, bid_price, rounding=ROUND_DOWN)
            ask_str = self.format_price(symbol, ask_price, rounding=ROUND_UP)

            opts = PlaceOrderOptions(post_only=True, stp_mode="CANCEL_AGGRESSOR")

            if not DRY_RUN:
                try:
                    b_res = await self.client.place_order(
                        symbol=symbol, side="BUY", order_type="LIMIT",
                        quantity=q_str, price=bid_str, time_in_force="GTC", options=opts,
                        confirmation="ack"
                    )
                    if hasattr(b_res, "order_id"):
                        new_order_ids.append(b_res.order_id)

                    a_res = await self.client.place_order(
                        symbol=symbol, side="SELL", order_type="LIMIT",
                        quantity=q_str, price=ask_str, time_in_force="GTC", options=opts,
                        confirmation="ack"
                    )
                    if hasattr(a_res, "order_id"):
                        new_order_ids.append(a_res.order_id)
                except OrderError as e:
                    logger.error(f"[{symbol}] Grid Placement Error ({getattr(e, 'error_code', 'N/A')}): {e}")

        self.open_grid_orders[symbol] = new_order_ids
    # -------------------------------------------------------------------------
    async def feed_prices_from_rest(self):
        """Background feeder: implied mark from public open-interest every FEED_INTERVAL_SECONDS.

        The testnet edge rejects `trades`/L2 subscriptions on /ws/v1, so this is the
        primary price feed; MarketDataClient trade ticks supplement it when available.
        """
        while self.running:
            try:
                oi_rows = await self.rest_client.get_open_interest()
                by_sid = {}
                for row in oi_rows or []:
                    if isinstance(row, dict) and row.get("symbol_id") in SYMBOL_IDS.values():
                        by_sid[row.get("symbol_id")] = row
                for symbol, sid in SYMBOL_IDS.items():
                    row = by_sid.get(sid)
                    if not row:
                        continue
                    oi = float(row.get("open_interest") or 0)
                    ccy = float(row.get("oi_ccy") or 0)
                    if oi > 0 and ccy > 0:
                        self.md_manager.push_tick(symbol, ccy / oi)
            except Exception as e:
                logger.debug(f"REST price feeder error: {e}")
            await asyncio.sleep(FEED_INTERVAL_SECONDS)

    async def evaluate_symbol(self, symbol: str):
        """Evaluate symbol using regime-based strategy routing."""
        # Check warmup
        df = self.md_manager.get_dataframe(symbol)
        if len(df) < 20:
            logger.info(f"[{symbol}] Insufficient price history ({len(df)}/20 candles). Warming up...")
            return

        # Detect regime
        regime = self._detect_regime(symbol)
        config = REGIME_CONFIG.get(regime)

        if not config or regime == "WARMUP":
            return

        # Check module position limits
        module = config["module"]
        if self._get_active_module_count(module) >= config["max_pos"]:
            return

        # Check portfolio heat
        if not self._check_portfolio_heat():
            return

        # Set leverage for this module
        try:
            await self.client.update_leverage(symbol, config["leverage"])
        except Exception as e:
            logger.warning(f"[{symbol}] Could not update leverage: {e}")

        # Get mid price
        mid_price = self.md_manager.current_mids.get(symbol)
        if not mid_price:
            return

        # Switch to new module (clears old orders if needed)
        await self._switch_module(symbol, module)

        # Route to appropriate module
        if module == "A":
            await self.execute_trend_module(symbol, mid_price)
        elif module == "B":
            await self.execute_grid_module(symbol, mid_price)
        elif module == "C":
            await self.execute_funding_module(symbol, mid_price)
        elif module == "D":
            await self.execute_breakout_module(symbol, mid_price)
        else:
            logger.info(f"[{symbol}] Regime {regime} -> Module {module}: Monitoring...")

    # -------------------------------------------------------------------------
    # WebSocket & Listener Handlers
    # -------------------------------------------------------------------------
    async def listen_order_updates(self):
        try:
            async for update in self.client.order_updates():
                logger.info(f"Order Update Stream Payload: {update}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in order updates consumer: {e}")

    async def setup_market_data_stream(self):
        """Subscribe to public channels for keepalive and strategy data."""
        # Subscribe to public channels for keepalive and strategy data
        try:
            await self.client.subscribe(["funding_rate", "open_interest", "volume"])
            logger.info("Subscribed to funding_rate, open_interest, volume for WebSocket keepalive")
        except Exception as e:
            logger.warning(f"Failed to subscribe to public channels: {e}")

        # Register callbacks for public market data
        self.client.on_funding_rate_update(self._handle_funding_rate)
        self.client.on_open_interest_snapshot(self._handle_open_interest)
        self.client.on_volume_snapshot(self._handle_volume)

        if os.getenv("GODARK_ENABLE_TRADES", "").lower() not in ("1", "true", "yes"):
            logger.info("Trade-tick WS disabled (edge rejects `trades` subs); using REST implied-mark feeder.")
            return
        self.md_client = MarketDataClient(base_url=WS_URL)
        await self.md_client.connect()

        for symbol in SYMBOLS:
            await self.md_client.subscribe_trades(
                symbol,
                callback=lambda tick, s=symbol: self.md_manager.push_tick(
                    s, float(tick.get("price", 0))
                ),
            )

    def _handle_funding_rate(self, update):
        """Handle funding rate updates from WS."""
        # update is a FundingRateUpdate object
        symbol = getattr(update, "symbol", None)
        rate = getattr(update, "funding_rate", None)
        if symbol and rate is not None:
            self.funding_rates[symbol] = float(rate)
            logger.debug(f"[{symbol}] Funding rate updated: {float(rate):.6f}")

    def _handle_open_interest(self, msg):
        """Handle open interest snapshot from WS."""
        # msg is a dict with open_interest_snapshot data
        for item in msg.get("data", []):
            symbol = item.get("symbol")
            oi = float(item.get("open_interest", 0))
            if symbol in self.oi_history:
                self.oi_history[symbol].append((time.time(), oi))

    def _handle_volume(self, msg):
        """Handle volume snapshot from WS."""
        for item in msg.get("data", []):
            symbol = item.get("symbol")
            vol = float(item.get("volume", 0))
            if symbol in self.volume_history:
                self.volume_history[symbol].append((time.time(), vol))

    async def run_trading_loop(self):
        logger.info("Initializing leverage levels across active symbols...")
        for symbol in SYMBOLS:
            try:
                await self.client.update_leverage(symbol, DEFAULT_LEVERAGE)
            except Exception as e:
                logger.warning(f"Could not update leverage for {symbol}: {e}")

        asyncio.create_task(self.listen_order_updates())
        asyncio.create_task(self.feed_prices_from_rest())
        await self.setup_market_data_stream()

        logger.info("Agent strategy loop fully activated.")
        while self.running:
            if self.system_health_accepting:
                for symbol in SYMBOLS:
                    try:
                        await self.evaluate_symbol(symbol)
                    except Exception as e:
                        logger.error(f"[{symbol}] Execution Error: {e}", exc_info=True)

            await asyncio.sleep(LOOP_INTERVAL_SECONDS)

    async def shutdown(self):
        logger.info("Initiating graceful agent shutdown...")
        self.running = False
        for symbol in SYMBOLS:
            await self.clear_grid_orders(symbol)
        if self.md_client:
            await self.md_client.disconnect()


# -----------------------------------------------------------------------------
# Main Runtime Loop with Connection Backoff
# -----------------------------------------------------------------------------
async def main():
    backoff = 2
    max_backoff = 60

    while True:
        try:
            logger.info("Connecting to GoDark Private WS and REST Endpoints...")
            async with GodarkClient(
                api_key_id=API_KEY_ID,
                api_secret=API_SECRET,
                passphrase=PASSPHRASE,
                base_url=WS_URL,
                transport=transport_config,
            ) as client:
                async with GodarkRestClient(
                    api_key_id=API_KEY_ID,
                    api_secret=API_SECRET,
                    passphrase=PASSPHRASE,
                    rest_base_url=REST_BASE_URL,
                ) as rest_client:
                    agent = QuantitativeTradingAgent(client, rest_client)
                    backoff = 2  # Reset exponential backoff on successful handshake
                    await agent.run_trading_loop()

        except AuthenticationError as e:
            logger.critical(f"FATAL AUTHENTICATION FAILURE: {e}. Stopping process.")
            break

        except SessionError as e:
            logger.warning(f"HPKE Session Error: {e}. Re-authenticating in {backoff}s...")

        except (GDXConnectionError, GDXTimeoutError, OSError) as e:
            logger.warning(f"Network Connection Lost: {e}. Retrying in {backoff}s...")

        except GodarkError as e:
            logger.error(f"SDK Specific Error: {e}. Retrying in {backoff}s...")

        except Exception as e:
            logger.error(f"Unhandled Agent Exception: {e}. Retrying in {backoff}s...", exc_info=True)

        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, max_backoff)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Agent process terminated manually.")