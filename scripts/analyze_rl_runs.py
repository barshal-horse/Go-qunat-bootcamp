import json
from collections import Counter
import pandas as pd
import numpy as np

def analyze_run(run_id, log_path, console_path):
    print("=" * 70)
    print(f"ANALYZING RUN: {run_id}")
    print("=" * 70)
    records = []
    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except:
                    pass
    
    df = pd.DataFrame(records)
    print(f"Total structured log events: {len(df)}")
    types = df["type"].value_counts().to_dict() if not df.empty and "type" in df.columns else {}
    print("Event distribution:", types)

    # 1. Order Flow & Fill Analysis
    grids = df[df["type"] == "grid"] if "type" in df.columns else pd.DataFrame()
    fills = df[df["type"] == "fill"] if "type" in df.columns else pd.DataFrame()
    trades = df[df["type"] == "trade"] if "type" in df.columns else pd.DataFrame()
    switches = df[df["type"] == "regime_switch"] if "type" in df.columns else pd.DataFrame()
    
    print("\n--- 1. ORDER FLOW & EXECUTION EFFICIENCY ---")
    print(f"Total Grid Cycles: {len(grids)}")
    print(f"Total Directional Trades Attempted: {len(trades)}")
    print(f"Total Executed Fills: {len(fills)}")
    total_quotes_placed = len(grids) * 4 # roughly 4 limit orders per grid
    fill_prob = (len(fills) / max(1, total_quotes_placed)) * 100
    print(f"Empirical Quote Fill Probability P(fill | quote): {fill_prob:.3f}%")
    
    # 2. Reward & PnL Dynamics
    print("\n--- 2. REWARD MATRIX & REVENUE ATTRIBUTION ---")
    if not fills.empty:
        for idx, row in fills.iterrows():
            sym = row.get("symbol")
            side = row.get("side")
            fp = row.get("fill_price")
            fq = row.get("fill_qty")
            rpnl = row.get("realized_pnl")
            fee = row.get("trading_fee")
            ts = row.get("timestamp")
            print(f"  [{ts}] {sym} {side} {fq} @ {fp} -> Realized: ${rpnl} | Fee: ${fee}")
        
        total_pnl = fills["realized_pnl"].dropna().astype(float).sum()
        total_fees = fills["trading_fee"].dropna().astype(float).sum()
        print(f"Net Realized Trading PnL: ${total_pnl:.2f}")
        print(f"Total Maker Fee Drag: ${total_fees:.4f}")
    else:
        print("  Zero fills recorded.")

    # 3. Directional Strategy Actions
    print("\n--- 3. DIRECTIONAL AGENT ACTIONS (Policy Output) ---")
    if not trades.empty:
        for idx, tr in trades.iterrows():
            print(f"  [{tr.get('timestamp')}] {tr.get('symbol')} {tr.get('side')} (Mod {tr.get('module')}, {tr.get('regime')}) "
                  f"Price: {tr.get('price')} | Qty: {tr.get('qty'):.4f} | SL: {tr.get('sl')} | TP: {tr.get('tp')}")
    else:
        print("  Zero directional trades recorded.")

    # 4. Console Telemetry & Hidden State Transition
    print("\n--- 4. MDP ENVIRONMENT TELEMETRY & SYSTEM STATE ---")
    with open(console_path, "r", encoding="utf-8", errors="ignore") as f:
        c_lines = f.readlines()
    
    c_errors = [l for l in c_lines if "[ERROR]" in l]
    c_warnings = [l for l in c_lines if "[WARNING]" in l]
    c_closes = [l for l in c_lines if "Routing position close" in l]
    c_close_success = [l for l in c_lines if "Position closed successfully" in l]
    c_buffer_warnings = [l for l in c_lines if "Stream buffer full" in l]
    c_funding_seeds = [l for l in c_lines if "Seeded funding rates" in l]
    
    print(f"Total Execution Lines: {len(c_lines)}")
    print(f"Close Routings Attempted: {len(c_closes)}")
    print(f"Close Orders Successfully Filled: {len(c_close_success)}")
    print(f"Close Rejection Failure Count: {len(c_closes) - len(c_close_success)}")
    print(f"Stream Buffer Overflow Warnings: {len(c_buffer_warnings)}")
    if c_funding_seeds:
        print(f"Initial Funding Rates: {c_funding_seeds[0].strip().split('GoDarkQuantAgent: ')[-1]}")
    
    # Error classification
    err_patterns = Counter()
    for e in c_errors:
        if "POST_ONLY_WOULD_CROSS" in e:
            err_patterns["POST_ONLY_WOULD_CROSS (Code 2018)"] += 1
        elif "OrderError" in e:
            err_patterns["OrderError_Generic"] += 1
        elif "ConnectionError" in e:
            err_patterns["Network_Transient"] += 1
        else:
            err_patterns[e.split("]")[-1].strip()[:50]] += 1
    print(f"Environment Errors: {dict(err_patterns)}")
    print("\n")

if __name__ == "__main__":
    analyze_run("RUN 1 (ID: 37825707179 | 18:37Z - 00:18Z)", "run_37825707179_logs/trades.log", "run_37825707179_console.log")
    analyze_run("RUN 2 (ID: 37893055385 | 06:20Z - 12:01Z)", "latest_run_logs/trades.log", "run_console.log")
