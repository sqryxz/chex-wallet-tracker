import requests
from datetime import datetime, timedelta
import json

def test_xrpscan_api():
    # Test different XRPScan API endpoints
    base_url = 'https://api.xrpscan.com/api/v1'
    
    # Test 1: Get account info for multiple known exchanges
    print("\nTest 1: Get Account Info for Major Exchanges")
    exchanges = {
        'Binance': 'rEb8TK3gBgk5auZkwc6sHnwrGVJH8DuaLh',
        'Bitstamp': 'rrpNnNLKrartuEqfJGpqyDwPj1AFPg9vn1',
        'Bitso': 'rG6FZ31hDHN1K5Dkbma3PSB5uVCuVVRzfn'
    }
    
    for name, address in exchanges.items():
        print(f"\nChecking {name} ({address})")
        endpoint = f"{base_url}/account/{address}"
        
        try:
            response = requests.get(endpoint)
            print(f"Status Code: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                print(f"Balance: {float(data.get('xrpBalance', 0)):,.2f} XRP")
                print(f"Sequence: {data.get('sequence', 'N/A')}")
                print(f"Reserve: {data.get('ownerCount', 0)} objects")
            else:
                print("Error Response:", response.text)
        except Exception as e:
            print(f"Error: {e}")
    
    # Test 2: Get account payment channels
    print("\nTest 2: Get Account Payment Channels")
    binance_address = 'rEb8TK3gBgk5auZkwc6sHnwrGVJH8DuaLh'
    endpoint = f"{base_url}/account/{binance_address}/channels"
    
    print(f"Requesting: {endpoint}")
    
    try:
        response = requests.get(endpoint)
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print("\nPayment Channels:")
            if isinstance(data, list):
                print(f"Found {len(data)} payment channels")
                if data:
                    channel = data[0]
                    print("\nSample Channel:")
                    print(f"Amount: {float(channel.get('amount', 0)) / 1_000_000:,.2f} XRP")
                    print(f"Destination: {channel.get('destination', 'N/A')}")
                    print(f"Channel ID: {channel.get('channel_id', 'N/A')}")
            else:
                print("No payment channels found")
        else:
            print("Error Response:", response.text)
    except Exception as e:
        print(f"Error: {e}")
    
    # Test 3: Get account assets/balances
    print("\nTest 3: Get Account Assets")
    endpoint = f"{base_url}/account/{binance_address}/assets"
    
    print(f"Requesting: {endpoint}")
    
    try:
        response = requests.get(endpoint)
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print("\nAssets:")
            if isinstance(data, list):
                print(f"Found {len(data)} assets")
                for asset in data[:2]:  # Show first 2 assets
                    print(f"\nCurrency: {asset.get('currency', 'N/A')}")
                    print(f"Balance: {asset.get('value', 'N/A')}")
                    print(f"Issuer: {asset.get('issuer', 'N/A')}")
            else:
                print("No assets found")
        else:
            print("Error Response:", response.text)
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_xrpscan_api() 