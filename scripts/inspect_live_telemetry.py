import asyncio
import os
import json
from dotenv import load_dotenv
from godark import GodarkRestClient

async def inspect():
    load_dotenv()
    client_kwargs = {
        'api_key_id': os.getenv('GODARK_API_KEY_ID', 'gdk_4e4e691ce1b5ab1ee1ccdaa7f3b09f88'),
        'api_secret': os.getenv('GODARK_API_SECRET', '4e9425b7920c4e11a161ad8e9af5330b31f160fd91d1c72ba9c1c12fa68d8375'),
        'passphrase': os.getenv('GODARK_PASSPHRASE', 'asdfghjkl'),
        'rest_base_url': os.getenv('GODARK_REST_URL', 'https://api.godark-dex.com'),
    }
    
    print("Connecting to GoDark REST Client...")
    async with GodarkRestClient(**client_kwargs) as client:
        print(f"Connected as account={client.account_str}!")
        
        # 1. Account Summary
        acc = await client.get_account()
        print("\n=== ACCOUNT STATE ===")
        print(f"Summary: {acc.summary}")

        
        # 2. Positions
        positions = await client.get_positions()
        p_rows = getattr(positions, 'rows', positions)
        print(f"\n=== POSITIONS ({len(p_rows)}) ===")
        for p in p_rows:
            print(f"  {getattr(p, 'symbol_id', getattr(p, 'symbol', 'N/A'))}: Side={getattr(p, 'side', 'N/A')} Size={getattr(p, 'quantity', getattr(p, 'size', 'N/A'))} Entry={getattr(p, 'entry_price', 'N/A')} Mark={getattr(p, 'mark_price', 'N/A')} uPnL={getattr(p, 'unrealized_pnl', 'N/A')}")
            
        # 3. Open Orders
        orders = await client.get_open_orders()
        o_rows = getattr(orders, 'rows', orders)
        print(f"\n=== OPEN ORDERS ({len(o_rows)}) ===")
        for o in o_rows:
            print(f"  ID={getattr(o, 'order_id', 'N/A')} | Sym={getattr(o, 'symbol_id', getattr(o, 'symbol', 'N/A'))} | Side={getattr(o, 'side', 'N/A')} | Qty={getattr(o, 'quantity', 'N/A')} | Px={getattr(o, 'price', 'N/A')} | Type={getattr(o, 'order_type', 'N/A')} | ReduceOnly={getattr(o, 'reduce_only', False)}")
            
        # 4. Funding Rates
        try:
            funding = await client.get_funding_rates()
            print(f"\n=== FUNDING RATES ({len(funding)}) ===")
            for f in funding:
                print(f"  {f.symbol}: Rate={f.funding_rate} NextSettlement={getattr(f, 'next_funding_time', getattr(f, 'timestamp', 'N/A'))}")
        except Exception as e:
            print(f"Funding rates note: {e}")

if __name__ == "__main__":
    asyncio.run(inspect())
