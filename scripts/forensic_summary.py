import os
import asyncio
import httpx
from dotenv import load_dotenv
from godark import GodarkRestClient
from collections import defaultdict

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
            by_date = defaultdict(lambda: {"pnl": 0.0, "fee": 0.0, "fills": 0, "wins": 0, "losses": 0})
            by_sym = defaultdict(lambda: {"pnl": 0.0, "fee": 0.0, "fills": 0})
            big_losses = []
            big_wins = []

            for page in range(30):
                url = "https://api.godark-dex.com/api/v1/orders/history?limit=100"
                if cursor:
                    url += f"&cursor={cursor}"
                resp = await client.get(url, headers=headers)
                if resp.status_code != 200:
                    break
                data = resp.json()
                rows = data.get("rows", [])
                if not rows:
                    break
                for o in rows:
                    d = o.get("detail", {})
                    q = float(d.get("filled_qty", 0) or 0)
                    if q > 0:
                        created = o.get("created_at", "")[:10]
                        sym = SYMBOL_BY_ID.get(o.get("symbol_id"), "OTHER")
                        pnl = float(d.get("realized_pnl") or 0)
                        fee = float(d.get("trading_fee") or 0)
                        by_date[created]["pnl"] += pnl
                        by_date[created]["fee"] += fee
                        by_date[created]["fills"] += 1
                        if pnl > 5:
                            by_date[created]["wins"] += 1
                            big_wins.append((created, sym, q, pnl, fee))
                        elif pnl < -5:
                            by_date[created]["losses"] += 1
                            big_losses.append((created, sym, q, pnl, fee))
                        by_sym[sym]["pnl"] += pnl
                        by_sym[sym]["fee"] += fee
                        by_sym[sym]["fills"] += 1
                cursor = data.get("next_cursor")
                if not cursor:
                    break
            
            print("=== DAILY BREAKDOWN ===")
            for d in sorted(by_date.keys()):
                v = by_date[d]
                net = v["pnl"] - v["fee"]
                print(f"{d} | Fills: {v['fills']:3d} | Realized PnL: ${v['pnl']:>10,.2f} | Fees: ${v['fee']:>9,.2f} | Net: ${net:>10,.2f} (Wins: {v['wins']}, Losses: {v['losses']})")
            
            print("\n=== SYMBOL BREAKDOWN ===")
            for s in sorted(by_sym.keys()):
                v = by_sym[s]
                net = v["pnl"] - v["fee"]
                print(f"{s:<15} | Fills: {v['fills']:3d} | PnL: ${v['pnl']:>10,.2f} | Fees: ${v['fee']:>9,.2f} | Net: ${net:>10,.2f}")
                
            print("\n=== TOP 10 LARGEST LOSSES ===")
            for l in sorted(big_losses, key=lambda x: x[3])[:10]:
                print(f"{l[0]} | {l[1]} | Qty: {l[2]} | PnL: ${l[3]:>10,.2f} | Fee: ${l[4]:>8,.2f}")

            print("\n=== TOP 10 LARGEST WINS ===")
            for w in sorted(big_wins, key=lambda x: x[3], reverse=True)[:10]:
                print(f"{w[0]} | {w[1]} | Qty: {w[2]} | PnL: ${w[3]:>10,.2f} | Fee: ${w[4]:>8,.2f}")

if __name__ == "__main__":
    asyncio.run(main())
