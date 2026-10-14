import json
import os
import glob

def analyze_file(filepath):
    if not os.path.exists(filepath):
        return
    print(f"\n==========================================")
    print(f"ANALYZING: {filepath}")
    print(f"==========================================")
    
    records = []
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except:
                    pass

    trades = [r for r in records if r.get('type') == 'trade']
    grids = [r for r in records if r.get('type') == 'grid']
    closes = [r for r in records if r.get('type') == 'close']
    fills = [r for r in records if r.get('type') == 'fill']
    
    print(f"Total Events: {len(records)} | Trades: {len(trades)} | Grids: {len(grids)} | Closes: {len(closes)} | Fills: {len(fills)}")

    total_realized = 0.0
    wins = []
    losses = []
    fees = 0.0

    for r in closes:
        pnl = r.get('unrealized_pnl')
        reason = r.get('reason', 'N/A')
        sym = r.get('symbol', 'N/A')
        side = r.get('side', 'N/A')
        qty = r.get('qty', 0.0)
        price = r.get('price', 0.0)
        if pnl is not None:
            pnl_val = float(pnl)
            total_realized += pnl_val
            if pnl_val >= 0:
                wins.append((sym, side, qty, price, pnl_val, reason))
            else:
                losses.append((sym, side, qty, price, pnl_val, reason))

    for f in fills:
        if f.get('trading_fee'):
            fees += float(f['trading_fee'])

    print(f"Total Realized PnL from closes: ${total_realized:,.2f}")
    print(f"Winning Closes ({len(wins)}): +${sum(w[4] for w in wins):,.2f}")
    print(f"Losing Closes ({len(losses)}): -${abs(sum(l[4] for l in losses)):,.2f}")
    print(f"Total Tracked Fees: ${fees:,.2f}")
    
    if losses:
        print("\nTop Losses:")
        sorted_losses = sorted(losses, key=lambda x: x[4])
        for sym, side, qty, price, pnl, reason in sorted_losses[:15]:
            print(f"  🔴 {sym} {side} qty={qty} @ {price:.2f} -> PnL: ${pnl:,.2f} | Reason: {reason}")

    if wins:
        print("\nTop Wins:")
        sorted_wins = sorted(wins, key=lambda x: x[4], reverse=True)
        for sym, side, qty, price, pnl, reason in sorted_wins[:10]:
            print(f"  🟢 {sym} {side} qty={qty} @ {price:.2f} -> PnL: +${pnl:,.2f} | Reason: {reason}")

def main():
    target_files = [
        "trades.log",
        "latest_run_logs/trades.log",
        "artifacts_run_37825707179/trades.log",
        "artifacts_run_37778627545/trades.log"
    ]
    for tf in target_files:
        analyze_file(tf)

if __name__ == '__main__':
    main()
