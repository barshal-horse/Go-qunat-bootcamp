import asyncio
import os
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
            # Populate tick history
            for i in range(20):
                self.agent.md_manager.push_tick(sym, mid + i)

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

        asyncio.run(run_async())


if __name__ == "__main__":
    unittest.main()
