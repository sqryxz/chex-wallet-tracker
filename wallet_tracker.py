import os
import json
from datetime import datetime, timedelta
import requests
from dotenv import load_dotenv
from web3 import Web3
import pandas as pd
import time
from collections import defaultdict
import networkx as nx

# Load environment variables
load_dotenv()

class WalletTracker:
    def __init__(self):
        # Load environment variables
        load_dotenv()
        
        self.etherscan_api_key = os.getenv('ETHERSCAN_API_KEY')
        self.infura_api_key = os.getenv('INFURA_API_KEY')
        
        if not self.etherscan_api_key:
            raise ValueError("Etherscan API key not found in .env file")
        if not self.infura_api_key:
            raise ValueError("Infura API key not found in .env file")
            
        print(f"Initializing Web3 with Infura key: {self.infura_api_key[:6]}...")
        
        self.chex_contract = '0x9Ce84F6A69986a83d92C324df10bC8E64771030f'
        try:
            # Connect to Ethereum mainnet
            infura_url = f"https://mainnet.infura.io/v3/{self.infura_api_key}"
            print(f"Connecting to Ethereum network...")
            self.w3 = Web3(Web3.HTTPProvider(infura_url))
            
            if not self.w3.is_connected():
                raise ConnectionError("Failed to connect to Ethereum network")
            print(f"Successfully connected to Ethereum network (Current block: {self.w3.eth.block_number})")
        except Exception as e:
            print(f"Error connecting to Ethereum network: {str(e)}")
            raise
        
        # Known exchange addresses and labels
        self.known_addresses = {
            # Exchanges
            '0x28C6c06298d514Db089934071355E5743bf21d60': 'Binance',
            '0x21a31Ee1afC51d94C2eFcCAa2092aD1028285549': 'Binance Cold Wallet',
            '0xdFd5293D8e347dFe59E90eFd55b2956a1343963d': 'Bitfinex',
            '0x1151314c646Ce4E0eFD76d1aF4760aE66a9Fe30F': 'Kraken',
            # Add more known exchange addresses as needed
        }
        
        # Transaction sequence tracking
        self.transaction_sequences = defaultdict(list)
        self.gas_price_history = []
        
        # Initialize other existing attributes
        self.xrp_addresses = {
            'Binance': 'rEb8TK3gBgk5auZkwc6sHnwrGVJH8DuaLh',
            'Bitstamp': 'rrpNnNLKrartuEqfJGpqyDwPj1AFPg9vn1',
            'Bitso': 'rG6FZ31hDHN1K5Dkbma3PSB5uVCuVVRzfn',
            'Binance Hot Wallet': 'rJb5KsHsDHF1YS5B5DU6QCkH5NsPaKQTcy',
            'Crypto.com': 'rU2mEJSLqBRkYLVTv55rFTgQajkLTnT6mA'
        }
        
        self.balance_history_file = 'xrp_balance_history.json'
        self.load_balance_history()

    def identify_wallet_cluster(self, address):
        """Identify if a wallet belongs to a known cluster (e.g., exchange)"""
        try:
            # Convert address to checksum format
            checksum_address = Web3.to_checksum_address(address)
            
            # Check direct matches
            if checksum_address in self.known_addresses:
                return self.known_addresses[checksum_address]
            
            # Get wallet code
            code = self.w3.eth.get_code(checksum_address)
            
            # Check if it's a contract
            if code != b'':
                # Get transaction count
                tx_count = self.w3.eth.get_transaction_count(checksum_address)
                
                if tx_count > 100000:  # High transaction count might indicate an exchange
                    return "Possible Exchange (High Activity)"
                
                return "Smart Contract"
            
            return "Unknown Wallet"
        except Exception as e:
            print(f"Error identifying wallet cluster for {address}: {str(e)}")
            return "Unknown"

    def analyze_transaction_sequence(self, address, hours=24):
        """Analyze transaction sequences for patterns"""
        endpoint = 'https://api.etherscan.io/api'
        
        # Get both CHEX transfers and general transactions
        params = {
            'module': 'account',
            'action': 'tokentx',
            'address': address,
            'startblock': 0,
            'endblock': 99999999,
            'sort': 'desc',
            'apikey': self.etherscan_api_key
        }
        
        response = requests.get(endpoint, params=params)
        if response.status_code != 200:
            return []
        
        data = response.json()
        if data['status'] != '1':
            return []
        
        transactions = data['result']
        
        # Create a timeline of transactions
        timeline = []
        current_time = datetime.now()
        cutoff_time = current_time - timedelta(hours=hours)
        
        for tx in transactions:
            tx_time = datetime.fromtimestamp(int(tx['timeStamp']))
            if tx_time < cutoff_time:
                break
                
            timeline.append({
                'timestamp': tx_time,
                'token': tx['tokenSymbol'],
                'amount': float(self.w3.from_wei(int(tx['value']), 'ether')),
                'type': 'in' if tx['to'].lower() == address.lower() else 'out',
                'gas_price': int(tx['gasPrice']),
                'gas_used': int(tx['gasUsed'])
            })
        
        return timeline

    def detect_unusual_gas_patterns(self, transactions):
        """Detect unusual gas usage patterns that might indicate arbitrage or malicious activity"""
        if not transactions:
            return []
        
        # Calculate gas statistics
        gas_prices = [tx['gas_price'] for tx in transactions]
        avg_gas = sum(gas_prices) / len(gas_prices)
        std_gas = pd.Series(gas_prices).std()
        
        unusual_patterns = []
        
        for i, tx in enumerate(transactions):
            # Check for gas price spikes
            if tx['gas_price'] > avg_gas + (2 * std_gas):
                unusual_patterns.append({
                    'type': 'High Gas Price',
                    'timestamp': tx['timestamp'],
                    'gas_price': tx['gas_price'],
                    'average_gas': avg_gas,
                    'deviation': (tx['gas_price'] - avg_gas) / std_gas
                })
            
            # Check for rapid successive transactions
            if i > 0:
                time_diff = (transactions[i-1]['timestamp'] - tx['timestamp']).total_seconds()
                if time_diff < 60 and tx['gas_price'] > avg_gas:  # Within 1 minute with above-average gas
                    unusual_patterns.append({
                        'type': 'Rapid Transactions',
                        'timestamp': tx['timestamp'],
                        'time_difference': time_diff,
                        'gas_price': tx['gas_price']
                    })
        
        return unusual_patterns

    def analyze_wallet_movement(self, address, hours=24):
        """Comprehensive analysis of wallet movement patterns"""
        # Get transaction sequence
        tx_sequence = self.analyze_transaction_sequence(address, hours)
        
        # Identify wallet cluster
        cluster_type = self.identify_wallet_cluster(address)
        
        # Analyze gas patterns
        unusual_gas = self.detect_unusual_gas_patterns(tx_sequence)
        
        # Calculate basic statistics
        if tx_sequence:
            total_in = sum(tx['amount'] for tx in tx_sequence if tx['type'] == 'in')
            total_out = sum(tx['amount'] for tx in tx_sequence if tx['type'] == 'out')
            total_gas = sum(tx['gas_price'] * tx['gas_used'] for tx in tx_sequence)
            
            return {
                'address': address,
                'cluster_type': cluster_type,
                'total_incoming': total_in,
                'total_outgoing': total_out,
                'transaction_count': len(tx_sequence),
                'total_gas_spent': self.w3.from_wei(total_gas, 'ether'),
                'unusual_patterns': unusual_gas,
                'transaction_sequence': tx_sequence
            }
        
        return None

    def load_balance_history(self):
        """Load historical balance data from file"""
        try:
            with open(self.balance_history_file, 'r') as f:
                self.balance_history = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.balance_history = {}
            
    def save_balance_history(self):
        """Save historical balance data to file"""
        with open(self.balance_history_file, 'w') as f:
            json.dump(self.balance_history, f, indent=2)
            
    def get_chex_transfers(self, hours=24):
        """Fetch CHEX token transfers for the last n hours"""
        endpoint = 'https://api.etherscan.io/api'
        
        # Calculate timestamp for n hours ago
        current_time = datetime.now()
        start_time = int((current_time - timedelta(hours=hours)).timestamp())
        
        params = {
            'module': 'account',
            'action': 'tokentx',
            'contractaddress': self.chex_contract,
            'starttime': start_time,
            'sort': 'desc',
            'apikey': self.etherscan_api_key
        }
        
        print(f"Fetching CHEX transfers since {datetime.fromtimestamp(start_time)}")
        response = requests.get(endpoint, params=params)
        if response.status_code == 200:
            data = response.json()
            print(f"Etherscan API response status: {data.get('status')}")
            print(f"Etherscan API message: {data.get('message')}")
            if data['status'] == '1':
                return data['result']
            else:
                print(f"Error from Etherscan API: {data.get('result', 'Unknown error')}")
        else:
            print(f"HTTP error {response.status_code} from Etherscan API")
        return []

    def get_xrp_movements(self, hours=24):
        """Track XRP balance changes for major exchanges with historical comparison"""
        base_url = 'https://api.xrpscan.com/api/v1'
        movements = []
        current_time = int(datetime.now().timestamp())
        
        # Get current balances and update history
        print(f"\nFetching XRP exchange balances")
        current_balances = {}
        
        for name, address in self.xrp_addresses.items():
            try:
                endpoint = f"{base_url}/account/{address}"
                response = requests.get(endpoint)
                
                if response.status_code == 200:
                    data = response.json()
                    balance = float(data.get('xrpBalance', 0))
                    current_balances[address] = balance
                    print(f"{name}: {balance:,.2f} XRP")
                    
                    # Update balance history
                    if address not in self.balance_history:
                        self.balance_history[address] = []
                    
                    # Add new balance point
                    self.balance_history[address].append({
                        'timestamp': current_time,
                        'balance': balance
                    })
                    
                    # Clean up old history (keep last 7 days)
                    cutoff_time = current_time - (7 * 24 * 3600)
                    self.balance_history[address] = [
                        point for point in self.balance_history[address]
                        if point['timestamp'] > cutoff_time
                    ]
                    
                    # Compare with historical balances
                    historical_points = self.balance_history[address]
                    for point in historical_points:
                        if current_time - point['timestamp'] <= hours * 3600:
                            change = balance - point['balance']
                            if abs(change) >= 50000:  # Lowered threshold to 50k XRP
                                movements.append({
                                    'source': name if change < 0 else 'Unknown',
                                    'destination': 'Unknown' if change < 0 else name,
                                    'amount': abs(change),
                                    'timestamp': current_time,
                                    'previous_balance': point['balance'],
                                    'current_balance': balance,
                                    'time_difference': f"{(current_time - point['timestamp']) / 3600:.1f} hours"
                                })
                else:
                    print(f"Error fetching balance for {name}: {response.text}")
            except Exception as e:
                print(f"Error processing {name}: {e}")
        
        # Save updated balance history
        self.save_balance_history()
        return movements

    def process_large_movements(self, token_type, movements, min_amount):
        """Process and filter large movements"""
        print(f"\nProcessing {token_type} movements...")
        print(f"Received {len(movements)} transactions to process")
        
        large_moves = []
        
        for tx in movements:
            try:
                if token_type == 'CHEX':
                    amount = float(self.w3.from_wei(int(tx['value']), 'ether'))
                    if amount >= min_amount:
                        large_moves.append({
                            'token': 'CHEX',
                            'from': tx['from'],
                            'to': tx['to'],
                            'amount': amount,
                            'timestamp': datetime.fromtimestamp(int(tx['timeStamp']))
                        })
                elif token_type == 'XRP':
                    amount = float(tx['amount'])
                    if amount >= min_amount:
                        large_moves.append({
                            'token': 'XRP',
                            'from': tx['source'],
                            'to': tx['destination'],
                            'amount': amount,
                            'timestamp': datetime.fromtimestamp(tx['timestamp']),
                            'details': f"Balance change: {tx['previous_balance']:,.2f} → {tx['current_balance']:,.2f} ({tx['time_difference']} ago)"
                        })
            except Exception as e:
                print(f"Error processing {token_type} transaction: {e}")
                print(f"Transaction data: {tx}")
                continue
        
        print(f"Found {len(large_moves)} large {token_type} movements")
        return pd.DataFrame(large_moves) if large_moves else pd.DataFrame(columns=['token', 'from', 'to', 'amount', 'timestamp', 'details'])

    def generate_enhanced_report(self, hours=24):
        """Generate an enhanced report including wallet clustering and pattern analysis"""
        # Get basic CHEX transfers
        chex_moves = self.get_chex_transfers(hours)
        
        # Analyze each unique address involved in large transfers
        analyzed_addresses = set()
        detailed_analysis = []
        
        for tx in chex_moves:
            for address in [tx['from'], tx['to']]:
                if address not in analyzed_addresses:
                    analysis = self.analyze_wallet_movement(address, hours)
                    if analysis:
                        detailed_analysis.append(analysis)
                    analyzed_addresses.add(address)
        
        # Generate report
        report = f"Enhanced Wallet Movement Report - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        
        # Wallet Clustering Section
        report += "=== Wallet Clustering Analysis ===\n"
        for analysis in detailed_analysis:
            report += f"\nAddress: {analysis['address']}\n"
            report += f"Cluster Type: {analysis['cluster_type']}\n"
            report += f"Total CHEX In: {analysis['total_incoming']:,.2f}\n"
            report += f"Total CHEX Out: {analysis['total_outgoing']:,.2f}\n"
            report += f"Transaction Count: {analysis['transaction_count']}\n"
            report += f"Total Gas Spent: {analysis['total_gas_spent']:.4f} ETH\n"
        
        # Unusual Patterns Section
        report += "\n=== Unusual Activity Patterns ===\n"
        for analysis in detailed_analysis:
            if analysis['unusual_patterns']:
                report += f"\nAddress: {analysis['address']}\n"
                for pattern in analysis['unusual_patterns']:
                    report += f"- {pattern['type']} detected at {pattern['timestamp']}\n"
                    if 'gas_price' in pattern:
                        report += f"  Gas Price: {self.w3.from_wei(pattern['gas_price'], 'gwei')} gwei\n"
                    if 'time_difference' in pattern:
                        report += f"  Time between transactions: {pattern['time_difference']:.1f} seconds\n"
        
        return report

def main():
    try:
        tracker = WalletTracker()
        
        # Generate enhanced report
        report = tracker.generate_enhanced_report()
        
        # Print report to console
        print("\nFinal Report:")
        print("-" * 80)
        print(report)
        print("-" * 80)
        
        # Save report to file
        filename = f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(filename, 'w') as f:
            f.write(report)
        print(f"\nReport saved to {filename}")
        
    except Exception as e:
        print(f"Error running wallet tracker: {e}")

if __name__ == "__main__":
    main() 