import asyncio
import os
from collections import defaultdict
from dotenv import load_dotenv
import httpx
from godark import GodarkRestClient

load_dotenv()
SYMBOL_BY_ID = {1: 'BTC-USDC-PERP', 2: 'ETH-USDC-PERP', 5: 'SOL-USDC-PERP'}

async def main():
    async with GodarkRestClient(
        api_key_id=os.getenv('GODARK_API_KEY_ID'),
        api_secret=os.getenv('GODARK_API_SECRET'),
        passphrase=os.getenv('GODARK_PASSPHRASE'),
    ) as rest:
        token = rest.bearer_token
        headers = {'Authorization': f'Bearer {token}'}
        async with httpx.AsyncClient(timeout=15.0) as client:
            cursor = None
            all_fills = []
            for page in range(35):
                url = 'https://api.godark-dex.com/api/v1/orders/history?limit=100'
                if cursor:
                    url += f'&cursor={cursor}'
                r = await client.get(url, headers=headers)
                if r.status_code != 200:
                    break
                data = r.json()
                rows = data.get('rows', [])
                if not rows:
                    break
                for o in rows:
                    d = o.get('detail', {})
                    q = float(d.get('filled_qty', 0) or 0)
                    if q > 0:
                        all_fills.append({
                            'id': o.get('order_id'),
                            'sym': SYMBOL_BY_ID.get(o.get('symbol_id'), 'OTHER'),
                            'side': str(d.get('side', '')),
                            'qty': q,
                            'price': float(d.get('avg_fill_price') or d.get('price') or 0),
                            'pnl': float(d.get('realized_pnl') or 0),
                            'fee': float(d.get('trading_fee') or 0),
                            'time': o.get('created_at', ''),
                            'post_only': d.get('post_only', False),
                            'reduce_only': d.get('reduce_only', False)
                        })
                cursor = data.get('next_cursor')
                if not cursor:
                    break

            print(f"Total fills fetched: {len(all_fills)}")
            stats = defaultdict(lambda: {
                'count': 0, 'pnl': 0.0, 'fee': 0.0,
                'wins': 0, 'losses': 0, 'win_pnl': 0.0, 'loss_pnl': 0.0
            })
            for f in all_fills:
                k = (f['sym'], f['side'])
                stats[k]['count'] += 1
                stats[k]['pnl'] += f['pnl']
                stats[k]['fee'] += f['fee']
                if f['pnl'] > 0.01:
                    stats[k]['wins'] += 1
                    stats[k]['win_pnl'] += f['pnl']
                elif f['pnl'] < -0.01:
                    stats[k]['losses'] += 1
                    stats[k]['loss_pnl'] += f['pnl']

            print("=== BREAKDOWN BY SYMBOL & SIDE ===")
            for k, v in sorted(stats.items()):
                net = v['pnl'] - v['fee']
                print(f"{k[0]:<15} {k[1]:<4} | Fills: {v['count']:3d} | Net PnL: ${net:>10,.2f} "
                      f"(Gross PnL: ${v['pnl']:>10,.2f}, Fee: ${v['fee']:>8,.2f}) | "
                      f"Wins: {v['wins']} (+${v['win_pnl']:>9,.2f}) | Losses: {v['losses']} (${v['loss_pnl']:>9,.2f})")

            # Analyze holding duration & entry/exit pairing
            print("\n=== RECENT CLOSING TRADES & PNL ===")
            closing_fills = [f for f in all_fills if abs(f['pnl']) > 0.01]
            for f in closing_fills[:25]:
                print(f"[{f['time'][:19]}] {f['sym']:<15} {f['side']:<4} qty={f['qty']:<10.4f} "
                      f"@ ${f['price']:>10,.2f} | PnL: ${f['pnl']:>10,.2f} | Fee: ${f['fee']:>7,.2f}")

if __name__ == '__main__':
    asyncio.run(main())
