import os
import asyncio
import httpx
from datetime import datetime
from dotenv import load_dotenv
from godark import GodarkRestClient

load_dotenv()

SYMBOL_BY_ID = {1: "BTC-USDC-PERP", 2: "ETH-USDC-PERP", 5: "SOL-USDC-PERP"}

async def main():
    api_key_id = os.getenv("GODARK_API_KEY_ID")
    api_secret = os.getenv("GODARK_API_SECRET")
    passphrase = os.getenv("GODARK_PASSPHRASE")
    
    async with GodarkRestClient(
        api_key_id=api_key_id,
        api_secret=api_secret,
        passphrase=passphrase,
        rest_base_url="https://api.godark-dex.com",
    ) as rest:
        token = rest.bearer_token
        headers = {"Authorization": f"Bearer {token}"}
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            # 1. Account Summary
            acct = await rest.get_account()
            summary = getattr(acct, "summary", None) or (acct.get("summary", {}) if isinstance(acct, dict) else {})
            total_collateral = float(getattr(summary, "total_collateral", 0) or 0)
            free_collateral = float(getattr(summary, "free_collateral", 0) or 0)
            pos_margin = float(getattr(summary, "position_margin", 0) or 0)
            res_margin = float(getattr(summary, "reserved_order_margin", 0) or 0)

            print("=" * 70)
            print(f"ACCOUNT TOTAL COLLATERAL: ${total_collateral:,.2f} USDC (Free: ${free_collateral:,.2f})")
            print(f"POSITION MARGIN:          ${pos_margin:,.2f} | RESERVED: ${res_margin:,.2f}")
            print("=" * 70)

            # 2. Positions
            positions = await rest.get_positions()
            p_rows = getattr(positions, "rows", positions) or []
            print(f"\nACTIVE POSITIONS ({len(p_rows)}):")
            for p in p_rows:
                sym_id = getattr(p, "symbol_id", getattr(p, "symbol", "N/A"))
                sym = SYMBOL_BY_ID.get(sym_id, f"ID_{sym_id}")
                size = float(getattr(p, "quantity", getattr(p, "size", 0)) or 0)
                entry = float(getattr(p, "entry_price", 0) or 0)
                mark = float(getattr(p, "mark_price", 0) or 0)
                upnl = float(getattr(p, "unrealized_pnl", 0) or 0)
                side = str(getattr(p, "side", "N/A")).upper()
                print(f"  [{sym}] {side} {abs(size)} @ ${entry:,.4f} | Mark: ${mark:,.4f} | uPnL: ${upnl:+,.4f}")

            # 3. Open Orders
            orders = await rest.get_open_orders()
            o_rows = getattr(orders, "rows", orders) or []
            print(f"\nRESTING OPEN ORDERS ({len(o_rows)}):")
            for o in o_rows:
                sym_id = getattr(o, "symbol_id", getattr(o, "symbol", "N/A"))
                sym = SYMBOL_BY_ID.get(sym_id, f"ID_{sym_id}")
                side = str(getattr(o, "side", "N/A")).upper()
                q = float(getattr(o, "quantity", 0) or 0)
                px = float(getattr(o, "price", 0) or 0)
                ro = getattr(o, "reduce_only", False)
                oid = getattr(o, "order_id", "N/A")
                print(f"  [{sym}] {side:4s} {q:.4f} @ ${px:,.4f} (reduce_only={ro}) | ID: {oid}")

            # 4. Order History / Fills
            resp = await client.get("https://api.godark-dex.com/api/v1/orders/history?limit=100", headers=headers)
            if resp.status_code == 200:
                orders_data = resp.json().get("rows", resp.json().get("orders", []))
                filled = []
                for o in orders_data:
                    d = o.get("detail", {})
                    filled_qty = float(d.get("filled_qty", 0) or 0)
                    status = str(o.get("terminal_status", "")).upper()
                    if "FILL" in status or filled_qty > 0:
                        filled.append(o)
                
                print(f"\nFILLED ORDERS IN HISTORY ({len(filled)} of {len(orders_data)} recent orders):")
                if not filled:
                    print("  No fills recorded yet in recent history.")
                for o in filled:
                    d = o.get("detail", {})
                    sym_id = o.get("symbol_id")
                    sym = SYMBOL_BY_ID.get(sym_id, f"ID_{sym_id}")
                    side = str(d.get("side", "")).upper()
                    q = float(d.get("filled_qty") or d.get("quantity") or 0)
                    px = float(d.get("avg_fill_price") or d.get("price") or 0)
                    pnl = d.get("realized_pnl")
                    pnl_str = f"${float(pnl):+,.4f}" if pnl is not None else "None"
                    fee = d.get("trading_fee")
                    fee_str = f"${float(fee):,.4f}" if fee is not None else "None"
                    created = o.get("created_at", "")[:19]
                    status = o.get("terminal_status", "")
                    print(f"  [{created}] {sym} {side} {q} @ ${px:,.4f} | Status: {status} | Realized PnL: {pnl_str} | Fee: {fee_str}")

if __name__ == "__main__":
    asyncio.run(main())
