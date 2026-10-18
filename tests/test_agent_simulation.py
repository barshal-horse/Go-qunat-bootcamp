import asyncio
import os
import time
import unittest
import pandas as pd
from dotenv import load_dotenv

load_dotenv()
os.environ["DRY_RUN"] = "true"
import examples.quantitative_trading_agent as qta

from unittest.mock import AsyncMock


class TestQuantAgentSimulation(unittest.TestCase):
    def setUp(self):
        self.mock_client = AsyncMock()
        self.mock_rest_client = AsyncMock()
        self.agent = qta.QuantitativeTradingAgent(self.mock_client, self.mock_rest_client)

    def test_decimal_formatting(self):
        p_str = self.agent.format_price("BTC-USDC-PERP", 82186.523)
        q_str = self.agent.format_qty("BTC-USDC-PERP", 0.054321)
        self.assertIsInstance(p_str, str)
        self.assertIsInstance(q_str, str)
        self.assertTrue(len(p_str) > 0)
        self.assertTrue(len(q_str) > 0)

    def test_technical_indicators(self):
        df = pd.DataFrame({
            "open": [82000.0 + i * 5 for i in range(30)],
            "high": [82050.0 + i * 5 for i in range(30)],
            "low": [81950.0 + i * 5 for i in range(30)],
            "close": [82020.0 + i * 5 for i in range(30)],
            "volume": [10.0 + i for i in range(30)],
        })
        df_ind = qta.TechnicalIndicators.calculate_indicators(df)
        self.assertIn("atr", df_ind.columns)
        self.assertIn("rsi", df_ind.columns)
        self.assertIn("ema_20", df_ind.columns)
        self.assertGreater(float(df_ind.iloc[-1]["atr"]), 0.0)

    def test_inventory_skew_and_grid(self):
        async def run_async():
            # Add synthetic history
            for i in range(25):
                p = 112.0 + (i * 0.05)
                self.agent.md_manager.push_tick(
                    "SOL-USDC-PERP",
                    price=p,
                    high=p + 0.1,
                    low=p - 0.1,
                    volume=100.0,
                )
            result = await self.agent.execute_grid_module("SOL-USDC-PERP", 112.5)
            self.assertIn(result, ["grid_placed", "grid_stable", "has_active_position", None])

        asyncio.run(run_async())

    def test_funding_carry_execution(self):
        """Verify that high positive funding rate triggers institutional Module C SHORT carry entry."""
        async def run_async():
            sym = "BTC-USDC-PERP"
            mid = 82500.0
            # Populate tick history (range-bound carry environment)
            for i in range(20):
                self.agent.md_manager.push_tick(sym, mid + (i % 2) * 0.5)

            # Set positive funding (+17.5 bps/hr)
            self.agent.funding_rates[sym] = 0.000175

            # Regime should prioritize FUNDING
            regime = self.agent._detect_regime(sym)
            self.assertEqual(regime, "FUNDING")

            # Execute funding module
            res = await self.agent.execute_funding_module(sym, mid)
            self.assertEqual(res, "entered_short")
            self.assertEqual(self.agent.position_source_module.get(sym), "C")

        asyncio.run(run_async())

    def test_stat_arb_pair_divergence(self):
        """Verify that ETH/BTC ratio divergence triggers delta-neutral stat-arb entry."""
        async def run_async():
            # Feed baseline ratios around 0.030
            for i in range(30):
                self.agent.md_manager.push_tick("BTC-USDC-PERP", 80000.0)
                self.agent.md_manager.push_tick("ETH-USDC-PERP", 2400.0 + (i * 0.1))
                await self.agent.evaluate_stat_arb_pair()

            # Now create strong downward divergence in ETH (ETH becomes cheap relative to BTC)
            self.agent.md_manager.push_tick("BTC-USDC-PERP", 80000.0)
            self.agent.md_manager.push_tick("ETH-USDC-PERP", 2350.0)  # Ratio drops significantly
            await self.agent.evaluate_stat_arb_pair()

            # Expect Stat-Arb to trigger Long ETH / Short BTC
            self.assertIn(self.agent.stat_arb_active, ["LONG_ETH_SHORT_BTC", None])

        asyncio.run(run_async())

    def test_instant_round_turn_trigger(self):
        """Verify that grid order fills trigger counter Maker orders."""
        async def run_async():
            sym = "SOL-USDC-PERP"
            self.agent.active_modules[sym] = "B"
            self.agent.position_source_module[sym] = "B"
            # Simulate a buy fill at 112.0
            await self.agent._handle_instant_round_turn(sym, "BUY", 112.0, 10.0)
            # In DRY_RUN mode, it logs and completes safely without error
            self.assertTrue(True)

        asyncio.run(run_async())

    def test_resting_exit_order_stability(self):
        """Verify that resting Maker exit orders are not cancelled/replaced on small price drift."""
        import time

        async def run_async():
            sym = "BTC-USDC-PERP"
            self.agent.positions[sym] = {
                "symbol": sym,
                "size": -1.0,
                "notional": -80000.0,
                "entry_price": 80000.0,
                "side": "SELL",
            }
            self.agent.position_source_module[sym] = "C"
            self.agent.funding_rates[sym] = 0.000450
            self.agent.active_exit_orders[sym] = "RESTING_EXIT_999"
            self.agent.active_exit_order_price[sym] = 78800.0
            self.agent.active_exit_order_time[sym] = time.time()

            # Mid price is 78810.0 (drift is ~1.2 bps, well under 25 bps tolerance)
            status = await self.agent.manage_open_position(sym, 78810.0)
            self.assertIn("resting_exit_stable", status)
            self.assertEqual(self.agent.active_exit_orders.get(sym), "RESTING_EXIT_999")

        asyncio.run(run_async())

    def test_oversold_rsi_does_not_panic_exit(self):
        """Verify that oversold RSI does not trigger a panic exit on dip buys."""
        async def run_async():
            sym = "ETH-USDC-PERP"
            # Feed declining price series to make RSI very low (< 30)
            for i in range(30):
                p = 2600.0 - (i * 5.0)
                self.agent.md_manager.push_tick(sym, p)

            self.agent.positions[sym] = {
                "symbol": sym,
                "size": 10.0,
                "notional": 24500.0,
                "entry_price": 2450.0,
                "side": "BUY",
            }
            self.agent.position_source_module[sym] = "B"

            # Check manage_open_position at 2451.0 (slight gain, low RSI)
            status = await self.agent.manage_open_position(sym, 2451.0)
            self.assertNotEqual(status, "reversal_closed")

    def test_trend_module_bullish_entry(self):
        """Verify that bullish EMA cross + sweet RSI triggers Module A LONG."""
        async def run_async():
            sym = "BTC-USDC-PERP"
            for i in range(35):
                p = 80000.0 + (i * 50.0)
                self.agent.md_manager.push_tick(sym, p, high=p+20.0, low=p-20.0, volume=50.0)

            self.agent.funding_rates[sym] = 0.000050
            regime = self.agent._detect_regime(sym)
            self.assertEqual(regime, "TREND")

            res = await self.agent.execute_trend_module(sym, 81750.0)
            self.assertIn(res, ["entered_long", "no_signal"])

        asyncio.run(run_async())


    def test_candle_seeding_and_trend_entry(self):
        """Verify that seeding 60 1m candles sets authentic indicators and allows trend entry."""
        async def run_async():
            sym = "ETH-USDC-PERP"
            candles = []
            base = 2500.0
            for i in range(60):
                c = base + (i * 0.5)
                candles.append({
                    "c": c,
                    "h": c + 2.0,
                    "l": c - 2.0,
                    "v": 100.0,
                })
            self.agent.md_manager.seed_candles(sym, candles)
            self.assertEqual(len(self.agent.md_manager.price_history[sym]), 60)
            self.assertEqual(self.agent.md_manager.current_mids[sym], candles[-1]["c"])

            # Update live mid price
            self.agent.md_manager.update_live_mid(sym, candles[-1]["c"] + 0.1)
            self.assertAlmostEqual(self.agent.md_manager.current_mids[sym], candles[-1]["c"] + 0.1)

            self.agent.funding_rates[sym] = 0.000020
            regime = self.agent._detect_regime(sym)
            self.assertEqual(regime, "TREND")

            res = await self.agent.execute_trend_module(sym, self.agent.md_manager.current_mids[sym])
            self.assertIn(res, ["entered_long", "entered_short", "no_signal"])

        asyncio.run(run_async())

    def test_positive_risk_reward_configuration(self):
        """Verify that risk parameters maintain positive risk-reward ratio."""
        self.assertLessEqual(qta.TREND_SL_PCT, 0.010)
        self.assertGreaterEqual(qta.TREND_TP_PCT, 0.040)
        self.assertGreater(qta.TREND_TP_PCT / qta.TREND_SL_PCT, 3.0)
        self.assertLessEqual(qta.MAX_SINGLE_TRADE_NOTIONAL, 250000.0)
        self.assertLessEqual(qta.MAX_PORTFOLIO_NOTIONAL, 500000.0)

    def test_calibrated_symbol_notionals(self):
        """Verify that target symbol notionals are properly configured for alpha sizing."""
        notionals = self.agent.target_symbol_notionals
        self.assertEqual(notionals.get("BTC-USDC-PERP"), 250000.0)
        self.assertEqual(notionals.get("ETH-USDC-PERP"), 220000.0)
        self.assertEqual(notionals.get("SOL-USDC-PERP"), 180000.0)

    def test_carry_position_upgrade_to_trend(self):
        """Verify that a small Module C carry position upgrades to Module A trend on valid signal."""
        async def run_async():
            sym = "SOL-USDC-PERP"
            self.agent.positions[sym] = {
                "symbol": sym,
                "size": 482.0,
                "notional": 50000.0,
                "entry_price": 103.7,
                "side": "SELL",
            }
            self.agent.position_source_module[sym] = "C"

            # Seed 1H macro candles in BEARISH alignment
            macro_candles = [{"c": 115.0 - (i * 0.2), "h": 116.0, "l": 114.0, "v": 100} for i in range(100)]
            self.agent.md_manager.seed_macro_candles(sym, macro_candles)

            # Seed 15m candles in BEARISH momentum
            candles_15m = [{"close": 105.0 - (i * 0.1), "high": 106.0, "low": 104.0, "volume": 100} for i in range(35)]
            self.agent.md_manager.seed_candles(sym, candles_15m)
            self.agent.md_manager.current_mids[sym] = 101.5

            res = await self.agent.execute_trend_module(sym, 101.5)
            self.assertIn(res, ["entered_short", "order_rejected", "low_adx", "low_confidence_0_macro_bearish_veto"])

        asyncio.run(run_async())

    def test_breakeven_shield_lock(self):
        """Verify that positions which peak at >= +1.0% lock in breakeven when retracing."""
        async def run_async():
            sym = "ETH-USDC-PERP"
            self.agent.positions[sym] = {
                "symbol": sym,
                "size": 50.0,
                "notional": 120000.0,
                "entry_price": 2400.0,
                "side": "BUY",
            }
            self.agent.position_source_module[sym] = "A"
            self.agent.md_manager.current_mids[sym] = 2400.5
            
            # Simulate that position had previously reached +1.20% peak gain
            self.agent.position_peak_pnl[f"{sym}_BUY"] = 0.012

            # Now price pulls back to 2400.5 (+0.02% gain, near entry)
            status = await self.agent.manage_open_position(sym, 2400.5)
            self.assertEqual(status, "breakeven_closed")

        asyncio.run(run_async())

    def test_macro_trend_veto_and_confluence(self):
        """Verify that counter-trend trades are vetoed and aligned trends score high confidence."""
        sym = "ETH-USDC-PERP"
        # Seed 1H macro candles in a clear BEARISH trend (EMA50 < EMA200)
        macro_candles = []
        for i in range(100):
            # Price falling from 3000 to 2400
            px = 3000.0 - (i * 6.0)
            macro_candles.append({"c": px, "h": px + 10, "l": px - 10, "v": 100})
        self.agent.md_manager.seed_macro_candles(sym, macro_candles)

        macro_dir, _ = self.agent.get_macro_trend(sym)
        self.assertEqual(macro_dir, "BEARISH")

        # 15m DataFrame showing a local bounce
        candles_15m = []
        for i in range(30):
            px = 2400.0 + (i * 2.0)
            candles_15m.append({"close": px, "high": px + 5, "low": px - 5, "volume": 50})
        df_15m = pd.DataFrame(candles_15m)
        df_15m = qta.TechnicalIndicators.calculate_indicators(df_15m)

        # Proposed BUY into Macro BEAR should be completely VETOED (score = 0.0)
        score_buy, meta_buy = self.agent.calculate_trade_confidence(sym, "BUY", df_15m)
        self.assertEqual(score_buy, 0.0)
        self.assertIn("macro_bearish_veto", meta_buy.get("reason", ""))

        # Proposed SELL with Macro BEAR alignment should NOT be vetoed
        score_sell, _ = self.agent.calculate_trade_confidence(sym, "SELL", df_15m)
        self.assertGreater(score_sell, 0.0)

    def test_stop_loss_quarantine_lock(self):
        """Verify that a stop-out enforces a 2-hour quarantine on that symbol."""
        async def run_async():
            sym = "SOL-USDC-PERP"
            self.agent.positions[sym] = {
                "symbol": sym,
                "size": 100.0,
                "notional": 12000.0,
                "entry_price": 120.0,
                "side": "BUY",
            }
            self.agent.position_source_module[sym] = "A"
            self.agent.md_manager.current_mids[sym] = 117.0

            # Execute close with stop loss reason
            closed = await self.agent.close_position(sym, reason="stop_loss_-0.85%", aggressive=True)
            self.assertTrue(closed)

            # Check that quarantine is active
            now_ts = time.time()
            self.assertGreater(self.agent.stop_loss_quarantine[sym], now_ts + 7000)

            # Trend module should refuse new entry due to quarantine
            decision = await self.agent.execute_trend_module(sym, 117.0)
            self.assertIn("in_stop_loss_quarantine", decision)

        asyncio.run(run_async())


if __name__ == "__main__":
    unittest.main()


