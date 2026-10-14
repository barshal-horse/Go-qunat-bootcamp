import os
import asyncio
from dotenv import load_dotenv
from godark import GodarkRestClient
import httpx

load_dotenv()

async def main():
    async with GodarkRestClient(
        api_key_id=os.getenv("GODARK_API_KEY_ID"),
        api_secret=os.getenv("GODARK_API_SECRET"),
        passphrase=os.getenv("GODARK_PASSPHRASE"),
    ) as rest:
        token = rest.bearer_token
        headers = {"Authorization": f"Bearer {token}"}
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get("https://api.godark-dex.com/api/v1/account", headers=headers)
            if r.status_code == 200:
                acc = r.json()
                print("=== ACCOUNT SNAPSHOT ===")
                print(f"Total Collateral: ${float(acc.get('total_collateral_value', 0)):,.2f} USDC")
                print(f"Free Collateral:  ${float(acc.get('free_collateral', 0)):,.2f} USDC")
                print(f"Maintenance Req:  ${float(acc.get('maintenance_margin_requirement', 0)):,.2f} USDC")
            else:
                print("Failed to get account:", r.status_code, r.text)

            r_pos = await client.get("https://api.godark-dex.com/api/v1/positions", headers=headers)
            if r_pos.status_code == 200:
                positions = r_pos.json().get("positions", [])
                print(f"\n=== OPEN POSITIONS ({len(positions)}) ===")
                for p in positions:
                    sym = p.get("symbol", "")
                    side = p.get("side", "")
                    sz = float(p.get("size", 0) or 0)
                    entry = float(p.get("entry_price", 0) or 0)
                    mark = float(p.get("mark_price", 0) or 0)
                    upnl = float(p.get("unrealized_pnl", 0) or 0)
                    print(f"  {sym} {side} {sz} @ ${entry:,.2f} | Mark: ${mark:,.2f} | uPnL: ${upnl:+,.2f}")
            else:
                print("Failed to get positions:", r_pos.status_code, r_pos.text)

            r_ord = await client.get("https://api.godark-dex.com/api/v1/orders/open", headers=headers)
            if r_ord.status_code == 200:
                orders = r_ord.json().get("orders", [])
                print(f"\n=== OPEN RESTING ORDERS ({len(orders)}) ===")
                for o in orders:
                    d = o.get("detail", {})
                    print(f"  ID: {o.get('order_id')} | {d.get('symbol')} {d.get('side')} qty={d.get('quantity')} @ ${float(d.get('price', 0)):,.2f} post_only={d.get('post_only')}")
            else:
                print("Failed to get open orders:", r_ord.status_code)

if __name__ == "__main__":
    asyncio.run(main())
