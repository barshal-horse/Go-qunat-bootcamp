import os
import asyncio
import httpx
from dotenv import load_dotenv
from godark import GodarkRestClient

load_dotenv()

SYMBOL_BY_ID = {1: "BTC-USDC-PERP", 2: "ETH-USDC-PERP", 5: "SOL-USDC-PERP"}

async def main():
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    headers_gh = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json"
    }
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get("https://api.github.com/repos/barshal-horse/Go-qunat-bootcamp/actions/runs?per_page=3", headers=headers_gh)
        if r.status_code == 200:
            runs = r.json().get("workflow_runs", [])
            print("=== GITHUB ACTIONS RUNS ===")
            for run in runs:
                print(f"ID: {run['id']} | Status: {run['status']} | Conclusion: {run['conclusion']} | Created: {run['created_at']} | URL: {run['html_url']}")

    async with GodarkRestClient(
        api_key_id=os.getenv("GODARK_API_KEY_ID"),
        api_secret=os.getenv("GODARK_API_SECRET"),
        passphrase=os.getenv("GODARK_PASSPHRASE"),
    ) as rest:
        acct = await rest.get_account()
        print("\n=== ACCOUNT SUMMARY ===")
        summary = acct.summary
        print(f"Total Collateral: ${float(summary.total_collateral):,.2f}")
        print(f"Free Collateral:  ${float(summary.free_collateral):,.2f}")
        print(f"Position Margin:  ${float(summary.position_margin):,.2f}")
        print(f"Order Margin:     ${float(summary.reserved_order_margin):,.2f}")
        
        pos = await rest.get_positions()
        print("\n=== POSITIONS ===")
        rows = pos.rows if hasattr(pos, "rows") else []
        for p in rows:
            sym = SYMBOL_BY_ID.get(p.symbol_id, f"ID_{p.symbol_id}")
            print(f"Symbol: {sym:<15} | Side: {str(p.side):<15} | Size: {float(p.size):<10.4f} | Entry: ${float(p.entry_price):,.2f} | Mark: ${float(p.mark_price):,.2f} | uPnL: ${float(p.unrealized_pnl):+,.2f}")

        orders = await rest.get_open_orders()
        print(f"\n=== OPEN ORDERS ({len(orders.rows)}) ===")
        for o in orders.rows:
            sym = SYMBOL_BY_ID.get(o.symbol_id, f"ID_{o.symbol_id}")
            print(f"ID: {o.order_id} | Sym: {sym:<15} | Side: {o.side:<5} | Qty: {float(o.quantity):<10.4f} | Price: ${float(o.price):,.2f} | Status: {o.status}")

if __name__ == "__main__":
    asyncio.run(main())
