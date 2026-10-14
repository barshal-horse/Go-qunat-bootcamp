import os
import asyncio
import httpx
from dotenv import load_dotenv
from godark import GodarkRestClient

load_dotenv()
SYMBOL_BY_ID = {1: "BTC-USDC-PERP", 2: "ETH-USDC-PERP", 5: "SOL-USDC-PERP"}

async def main():
    async with GodarkRestClient(
        api_key_id=os.getenv("GODARK_API_KEY_ID"),
        api_secret=os.getenv("GODARK_API_SECRET"),
        passphrase=os.getenv("GODARK_PASSPHRASE"),
    ) as rest:
        token = rest.bearer_token
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get("https://api.godark-dex.com/api/v1/orders/history?limit=60", headers=headers)
            print("=== FILLS IN HISTORY (Newest to Oldest) ===")
            for o in r.json().get("rows", []):
                d = o.get("detail", {})
                if str(d.get("terminal_status", "")).upper() == "FILLED":
                    sym = SYMBOL_BY_ID.get(o.get("symbol_id"))
                    created = o.get("created_at", "")[:19]
                    px = float(d.get("avg_fill_price") or d.get("price") or 0)
                    pnl = float(d.get("realized_pnl") or 0)
                    fee = float(d.get("trading_fee") or 0)
                    qty = float(d.get("filled_qty") or 0)
                    side = str(d.get("side", ""))
                    print(f"[{created}] {sym:<15} {side:<5} {qty:<10.4f} @ ${px:,.2f} | PnL: ${pnl:+,.2f} | Fee: ${fee:,.2f}")

if __name__ == "__main__":
    asyncio.run(main())
