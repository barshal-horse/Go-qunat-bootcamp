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
        
        async with httpx.AsyncClient(timeout=15.0) as client:
            cursor = None
            total_filled = 0
            total_realized_pnl = 0.0
            total_fees = 0.0
            filled_trades = []
            
            for page in range(25):  # up to 25 pages = 2500 orders
                url = "https://api.godark-dex.com/api/v1/orders/history?limit=100"
                if cursor:
                    url += f"&cursor={cursor}"
                
                resp = await client.get(url, headers=headers)
                if resp.status_code != 200:
                    print(f"Error fetching page {page}: {resp.status_code}")
                    break
                
                data = resp.json()
                rows = data.get("rows", [])
                if not rows:
                    break
                
                for o in rows:
                    d = o.get("detail", {})
                    filled_qty = float(d.get("filled_qty", 0) or 0)
                    status = str(o.get("terminal_status", "")).upper()
                    if "FILL" in status or filled_qty > 0:
                        total_filled += 1
                        sym_id = o.get("symbol_id")
                        sym = SYMBOL_BY_ID.get(sym_id, f"ID_{sym_id}")
                        side = str(d.get("side", "")).upper()
                        q = float(d.get("filled_qty") or d.get("quantity") or 0)
                        px = float(d.get("avg_fill_price") or d.get("price") or 0)
                        pnl = float(d.get("realized_pnl") or 0)
                        fee = float(d.get("trading_fee") or 0)
                        created = o.get("created_at", "")[:19]
                        term_ts = o.get("terminal_timestamp", 0)
                        total_realized_pnl += pnl
                        total_fees += fee
                        filled_trades.append({
                            "created": created,
                            "symbol": sym,
                            "side": side,
                            "qty": q,
                            "price": px,
                            "pnl": pnl,
                            "fee": fee,
                            "status": status,
                            "term_ts": term_ts
                        })
                
                cursor = data.get("next_cursor")
                if not cursor:
                    break
            
            print(f"Total Filled Orders Found: {total_filled}")
            print(f"Total Cumulative Realized PnL: ${total_realized_pnl:+,.2f}")
            print(f"Total Cumulative Fees Paid:    ${total_fees:,.2f}")
            print(f"Net Realized:                  ${total_realized_pnl - total_fees:+,.2f}")
            print("\nRecent 30 Fills (newest to oldest):")
            for t in filled_trades[:30]:
                print(f"  [{t['created']}] {t['symbol']} {t['side']} {t['qty']} @ ${t['price']:,.4f} | PnL: ${t['pnl']:+,.4f} | Fee: ${t['fee']:,.4f}")

if __name__ == "__main__":
    asyncio.run(main())
