with open('examples/quantitative_trading_agent.py', 'r') as f:
    lines = f.readlines()

# Fix line 524 (index 523) - indent the logger.error line to 20 spaces
lines[523] = '                    logger.error(f"[{symbol}] Grid Placement Error ({getattr(e, \'error_code\', \'N/A\')}): {e}")\n'

with open('examples/quantitative_trading_agent.py', 'w') as f:
    f.writelines(lines)
print('Fixed')