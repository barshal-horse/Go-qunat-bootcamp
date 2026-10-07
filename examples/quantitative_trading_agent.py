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
RISK_FACTOR = 0.015
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

# Strategy Parameters
FUNDING_LONG_THRESHOLD = -0.0005
FUNDING_SHORT_THRESHOLD = 0.0005
FUNDING_EXIT_THRESHOLD = 0.0001
FUNDING_MAX_HOLD_HOURS = 24

OI_LOOKBACK_HOURS = 4
OI_SURGE_THRESHOLD = 0.20

KELTNER_PERIOD = 20
KELTNER_ATR_MULT = 2.0

REGIME_CONFIG = {
    "TREND":       {"module": "A", "weight": 0.45, "leverage": 3, "max_pos": 2},
    "MEAN_REV":    {"module": "B", "weight": 0.25, "leverage": 2, "max_pos": 3},
    "FUNDING":     {"module": "C", "weight": 0.15, "leverage": 2, "max_pos": 2},
    "BREAKOUT":    {"module": "D", "weight": 0.15, "leverage": 3, "max_pos": 1},
}
MAX_PORTFOLIO_HEAT = 0.80


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

        # EMAs
        df["ema_20"] = df["close"].ewm(span=20, adjust=False).mean()
        df["ema_50"] = df["close"].ewm(span=50, adjust=False).mean()

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
                        return val
        except Exception as e:
            logger.warning(f"Could not fetch account collateral via REST: {e}")
        return 10000.0

    async def sync_positions(self, _retried: bool = False):
        try:
            positions_data = await self.rest_client.get_positions()
            if positions_data:
                items = positions_data if isinstance(positions_data, list) else getattr(positions_data, "positions", [])
                new_positions = {}
                for pos in items:
                    sym = pos.get("symbol") if isinstance(pos, dict) else getattr(pos, "symbol", None)
                    size = float(pos.get("size", 0) if isinstance(pos, dict) else getattr(pos, "size", 0) or 0)
                    notional = float(pos.get("notional", 0) if isinstance(pos, dict) else getattr(pos, "notional", 0) or 0)
                    entry_price = float(pos.get("entry_price", 0) if isinstance(pos, dict) else getattr(pos, "entry_price", 0) or 0)
                    if sym and abs(size) > 0:
                        new_positions[sym] = {
                            "symbol": sym,
                            "size": size,
                            "notional": notional if notional != 0 else size * entry_price,
                            "entry_price": entry_price,
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
            max_notional = 10000.0 * 3.0
            heat = total_notional / max_notional if max_notional > 0 else 0
            return heat < MAX_PORTFOLIO_HEAT
        except Exception:
            return True

    def _get_active_module_count(self, module: str) -> int:
        return sum(1 for m in self.active_modules.values() if m == module)

    async def calculate_risk_position_size(self, symbol: str, entry_price: float, atr: float) -> float:
        collateral = await self.get_collateral_balance()
        risk_budget = collateral * RISK_FACTOR
        effective_atr = max(atr if not pd.isna(atr) else 0.0, entry_price * 0.002)
        sl_distance = 1.5 * effective_atr

        raw_qty = risk_budget / sl_distance
        # Cap notional at 80% of free-collateral margin so the venue never
        # rejects for MARGIN_INSUFFICIENT (fees + maintenance buffer)...
        max_notional = collateral * DEFAULT_LEVERAGE * 0.8
        # ...and never exceed the portfolio-heat budget minus exposure on book.
        current_notional = sum(abs(p.get("notional", 0.0)) for p in self.positions.values())
        heat_budget = max(0.0, MAX_PORTFOLIO_HEAT * 10000.0 * DEFAULT_LEVERAGE - current_notional)
        max_notional = min(max_notional, heat_budget)
        max_qty = max_notional / entry_price
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
        p_str = self.format_price(symbol, price)
        q_str = self.format_qty(symbol, qty)

        effective_atr = max(atr if not pd.isna(atr) else 0.0, price * 0.002)
        sl = price - (1.5 * effective_atr) if side == "BUY" else price + (1.5 * effective_atr)
        tp = price + (3.0 * effective_atr) if side == "BUY" else price - (3.0 * effective_atr)

        sl_str = self.format_price(symbol, sl)
        tp_str = self.format_price(symbol, tp)

        opts = PlaceOrderOptions(
            stop_loss_price=sl_str,
            take_profit_price=tp_str,
            stp_mode="CANCEL_AGGRESSOR",
        )

        logger.info(
            f"[{symbol}] MODULE {module} Signal -> Side: {side} | Price: {p_str} | Qty: {q_str} | "
            f"SL: {sl_str} | TP: {tp_str}"
        )

        if DRY_RUN:
            logger.info(f"[{symbol}] DRY_RUN enabled. Order execution simulated.")
            order_id = "DRY_RUN_" + str(int(time.time()))
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

            if not success:
                logger.error(f"[{symbol}] Order rejected by venue: error={error}")
                return False

            logger.info(f"[{symbol}] Order successfully accepted: order_id={order_id}")
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
            logger.error(f"[{symbol}] Unexpected order placement failure: {e}", exc_info=True)
            return False

    async def clear_grid_orders(self, symbol: str):
        order_ids = self.open_grid_orders.get(symbol, [])
        if not order_ids:
            return

        logger.info(f"[{symbol}] Clearing {len(order_ids)} stale Module B grid orders...")
        if not DRY_RUN:
            for oid in order_ids:
                try:
                    await self.client.cancel_order(str(oid), symbol)
                except Exception as e:
                    logger.debug(f"[{symbol}] Error cancelling order {oid}: {e}")
        self.open_grid_orders[symbol] = []

    async def execute_grid_module(self, symbol: str, mid_price: float):
        await self._switch_module(symbol, "B")
        config = REGIME_CONFIG["MEAN_REV"]
        if self._get_active_module_count("B") > config["max_pos"]:
            return "blocked_max_pos"
        if not self._check_portfolio_heat():
            return "blocked_heat"

        await self.clear_grid_orders(symbol)
        await self._set_leverage(symbol, config["leverage"])

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
            else:
                new_order_ids.extend(["SIM_BID_" + bid_str, "SIM_ASK_" + ask_str])

        self.open_grid_orders[symbol] = new_order_ids
        if new_order_ids:
            self.trade_logger.log_grid(
                symbol=symbol,
                bid_price=mid_price * 0.998,
                ask_price=mid_price * 1.002,
                qty=qty,
                order_ids=new_order_ids,
                module="B",
            )
            if self.telegram.enabled:
                msg = self.telegram.format_grid(
                    symbol=symbol,
                    bid_price=mid_price * 0.998,
                    ask_price=mid_price * 1.002,
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
        atr = float(latest["atr"]) if not pd.isna(latest["atr"]) else close * 0.002

        bullish_cross = (prev_ema20 <= prev_ema50) and (ema_20 > ema_50)
        bearish_cross = (prev_ema20 >= prev_ema50) and (ema_20 < ema_50)
        bullish_trend = (ema_20 > ema_50) and (close >= ema_20)
        bearish_trend = (ema_20 < ema_50) and (close <= ema_20)

        config = REGIME_CONFIG["TREND"]

        if (bullish_cross or bullish_trend) and (50.0 <= rsi <= 68.0):
            if self._get_active_module_count("A") >= config["max_pos"] or not self._check_portfolio_heat():
                return "blocked_risk"
            await self._switch_module(symbol, "A")
            await self._set_leverage(symbol, config["leverage"])
            qty = await self.calculate_risk_position_size(symbol, close, atr)
            ok = await self.place_directional_order(symbol, "BUY", close, qty, atr, module="A")
            return "entered_long" if ok else "order_rejected"

        elif (bearish_cross or bearish_trend) and (32.0 <= rsi <= 50.0):
            if self._get_active_module_count("A") >= config["max_pos"] or not self._check_portfolio_heat():
                return "blocked_risk"
            await self._switch_module(symbol, "A")
            await self._set_leverage(symbol, config["leverage"])
            qty = await self.calculate_risk_position_size(symbol, close, atr)
            ok = await self.place_directional_order(symbol, "SELL", close, qty, atr, module="A")
            return "entered_short" if ok else "order_rejected"

        return "no_signal"

    async def execute_funding_module(self, symbol: str, mid_price: float):
        funding = self.funding_rates.get(symbol, 0.0)
        df = self.md_manager.get_dataframe(symbol)
        if len(df) < 14:
            return "warming_up"

        df = TechnicalIndicators.calculate_indicators(df)
        latest = df.iloc[-1]
        rsi = float(latest["rsi"]) if not pd.isna(latest["rsi"]) else 50.0
        atr = float(latest["atr"]) if not pd.isna(latest["atr"]) else mid_price * 0.002

        # Exit condition: harvest profit once funding normalizes
        if symbol in self.positions:
            pos = self.positions[symbol]
            if abs(funding) < FUNDING_EXIT_THRESHOLD:
                logger.info(f"[{symbol}] Module C funding normalized ({funding:.6f}). Closing position...")
                close_side = "SELL" if pos["size"] > 0 else "BUY"
                ok = await self.place_directional_order(symbol, close_side, mid_price, abs(pos["size"]), atr, module="C_EXIT")
                return "exit_placed" if ok else "order_rejected"

        config = REGIME_CONFIG["FUNDING"]

        if funding <= FUNDING_LONG_THRESHOLD and rsi < 70:
            if self._get_active_module_count("C") >= config["max_pos"] or not self._check_portfolio_heat():
                return "blocked_risk"
            await self._switch_module(symbol, "C")
            await self._set_leverage(symbol, config["leverage"])
            qty = await self.calculate_risk_position_size(symbol, mid_price, atr)
            ok = await self.place_directional_order(symbol, "BUY", mid_price, qty, atr, module="C")
            logger.info(f"[{symbol}] Module C LONG: funding={funding:.6f}, RSI={rsi:.1f}")
            return "entered_long" if ok else "order_rejected"

        elif funding >= FUNDING_SHORT_THRESHOLD and rsi > 30:
            if self._get_active_module_count("C") >= config["max_pos"] or not self._check_portfolio_heat():
                return "blocked_risk"
            await self._switch_module(symbol, "C")
            await self._set_leverage(symbol, config["leverage"])
            qty = await self.calculate_risk_position_size(symbol, mid_price, atr)
            ok = await self.place_directional_order(symbol, "SELL", mid_price, qty, atr, module="C")
            logger.info(f"[{symbol}] Module C SHORT: funding={funding:.6f}, RSI={rsi:.1f}")
            return "entered_short" if ok else "order_rejected"

        return "no_signal"

    async def execute_breakout_module(self, symbol: str, mid_price: float):
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
        atr = float(latest.get("atr", 0) or mid_price * 0.002)

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
        oi_change = self._get_oi_change(symbol)

        if funding >= FUNDING_SHORT_THRESHOLD:
            return "FUNDING"
        elif adx > 25:
            return "TREND"
        elif oi_change > OI_SURGE_THRESHOLD or self._volume_surge(symbol):
            return "BREAKOUT"
        else:
            return "MEAN_REV"

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

        module = config["module"]
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

    async def run_trading_loop(self):
        swept = await self._startup_order_sweep()

        logger.info("Initializing leverage levels across active symbols...")
        for symbol in SYMBOLS:
            await self._set_leverage(symbol, DEFAULT_LEVERAGE)

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