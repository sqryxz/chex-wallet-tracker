import os
from datetime import datetime, timedelta
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def test_etherscan_api():
    api_key = os.getenv('ETHERSCAN_API_KEY')
    if not api_key:
        print("Error: No API key found in .env file")
        return
    
    print(f"Using API key: {api_key[:6]}...{api_key[-4:]}")
    
    # Test 1: Simple API test - Get latest Ethereum block number
    print("\nTest 1: Basic API Connection - Get Latest Block")
    endpoint = 'https://api.etherscan.io/api'
    params = {
        'module': 'proxy',
        'action': 'eth_blockNumber',
        'apikey': api_key
    }
    
    response = requests.get(endpoint, params=params)
    print(f"Status Code: {response.status_code}")
    print(f"Response: {response.json()}")
    
    # Test 2: CHEX Token Transfers
    print("\nTest 2: CHEX Token Transfers")
    chex_contract = '0x9Ce84F6A69986a83d92C324df10bC8E64771030f'
    
    # Get transfers from last 24 hours
    current_time = datetime.now()
    start_time = int((current_time - timedelta(hours=24)).timestamp())
    
    params = {
        'module': 'account',
        'action': 'tokentx',
        'contractaddress': chex_contract,
        'page': 1,
        'offset': 10,  # Get up to 10 transactions
        'starttime': start_time,
        'sort': 'desc',
        'apikey': api_key
    }
    
    print("\nMaking request with parameters:")
    for key, value in params.items():
        print(f"{key}: {value}")
    
    response = requests.get(endpoint, params=params)
    data = response.json()
    
    print(f"\nStatus Code: {response.status_code}")
    print(f"Status: {data.get('status')}")
    print(f"Message: {data.get('message')}")
    
    if data.get('status') == '1':
        transfers = data.get('result', [])
        print(f"\nFound {len(transfers)} transfers in the last 24 hours")
        if transfers:
            print("\nLatest transfer:")
            tx = transfers[0]
            print(f"From: {tx['from']}")
            print(f"To: {tx['to']}")
            print(f"Value: {tx['value']} wei")
            print(f"Timestamp: {datetime.fromtimestamp(int(tx['timeStamp']))}")
    else:
        print(f"Error: {data.get('result', 'Unknown error')}")
        print("\nFull response:")
        print(data)

if __name__ == "__main__":
    test_etherscan_api() 