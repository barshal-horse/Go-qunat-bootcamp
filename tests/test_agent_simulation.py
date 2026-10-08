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


if __name__ == "__main__":
    unittest.main()
