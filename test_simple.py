#!/usr/bin/env python3
import asyncio
from dotenv import load_dotenv
from godark import GodarkClient, Environment, Side, OrderType, TimeInForce

async def test():
    load_dotenv()
    print('dotenv loaded')
    
    client_kwargs = {
        'api_key_id': 'gdk_4e4e691ce1b5ab1ee1ccdaa7f3b09f88',
        'api_secret': '4e9425b7920c4e11a161ad8e9af5330b31f160fd91d1c72ba9c1c12fa68d8375',
        'passphrase': 'asdfghjkl',
        'base_url': 'wss://api.godark-dex.com',
    }
    
    try:
        async with GodarkClient(**client_kwargs) as client:
            print(f'Connected as account={client.account or ""}')
            await client.subscribe(['orders'])
            print('Subscribed to orders')
            await asyncio.sleep(0.5)
            
            # Test placing a LIMIT order with string price
            ack = await client.place_order(
                'BTC-USDC-PERP',
                Side.SELL,
                OrderType.LIMIT,
                '0.01',
                price='65000.50',
                time_in_force=TimeInForce.GTC,
            )
            print(f'Order placed: success={ack.success}, order_id={ack.order_id}, error={ack.error or ack.error_code}')
            
            if ack.success and ack.order_id:
                await client.cancel_order(str(ack.order_id), 'BTC-USDC-PERP')
                print('Order cancelled')
    except Exception as e:
        print(f'Error: {e}')
        import traceback
        traceback.print_exc()
    
    print('Test completed')

if __name__ == "__main__":
    asyncio.run(test())