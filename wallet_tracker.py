import os
import json
from datetime import datetime, timedelta
import requests
from dotenv import load_dotenv
import pandas as pd
import time
from collections import defaultdict, deque
import networkx as nx
import asyncio
import aiohttp
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Dict, List, Set, Any, Optional, Tuple
import logging
from dataclasses import dataclass, field
from itertools import islice
import math
from web3 import Web3

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Constants for optimization
CHUNK_SIZE = 5  # Increased from 2 to 5 for faster batch processing
MAX_WORKERS = 4  # Increased from 2 to 4 for more parallel processing
CACHE_TTL = 3600  # Cache TTL in seconds
MAX_CONCURRENT_REQUESTS = 3  # Increased from 2 to 3 for more concurrent API calls
REQUEST_DELAY = 3.0  # Reduced from 5.0 to 3.0 seconds between requests
ETHERSCAN_RATE_LIMIT = 0.33  # Increased to allow ~3 requests per 10 seconds (within Etherscan's limits)
MAX_RETRIES = 3  # Keep retries at 3
RETRY_DELAY = 5  # Keep initial retry delay at 5 seconds
QUICK_MODE_THRESHOLD = 2000  # Increased from 1000 to 2000 transactions in quick mode
GAS_PERCENTILE_THRESHOLD = 95  # Keep gas threshold at 95th percentile

def to_checksum_address(address: str) -> str:
    """Convert address to checksum format"""
    address = address.lower().replace('0x', '')
    address_hash = ''
    
    # Compute keccak-256 hash of the address
    from hashlib import sha3_256
    address_bytes = bytes.fromhex(address)
    keccak = sha3_256()
    keccak.update(address_bytes)
    address_hash = keccak.hexdigest()
    
    ret = '0x'
    
    for i in range(40):
        # If ith character is 9 or more && hash[i] is 1, uppercase
        if int(address_hash[i], 16) >= 8 and address[i].isalpha():
            ret += address[i].upper()
        else:
            ret += address[i].lower()
    
    return ret

def from_wei(value: int, unit: str = 'ether') -> float:
    """Convert wei value to ether or gwei"""
    if unit == 'ether':
        return float(value) / 1e18
    elif unit == 'gwei':
        return float(value) / 1e9
    else:
        raise ValueError("Unsupported unit. Use 'ether' or 'gwei'")

def to_wei(value: float, unit: str = 'ether') -> int:
    """Convert ether or gwei value to wei"""
    if unit == 'ether':
        return int(value * 1e18)
    elif unit == 'gwei':
        return int(value * 1e9)
    else:
        raise ValueError("Unsupported unit. Use 'ether' or 'gwei'")

@dataclass
class TransactionCache:
    """Cache for transaction data with TTL"""
    data: Dict[str, Any] = field(default_factory=dict)
    timestamps: Dict[str, float] = field(default_factory=dict)
    max_size: int = 10000
    ttl: int = CACHE_TTL
    
    def get(self, key: str) -> Optional[Any]:
        if key in self.data and time.time() - self.timestamps[key] < self.ttl:
            return self.data[key]
        return None
    
    def set(self, key: str, value: Any) -> None:
        current_time = time.time()
        
        # Clean expired entries if cache is full
        if len(self.data) >= self.max_size:
            self._cleanup(current_time)
        
        self.data[key] = value
        self.timestamps[key] = current_time
    
    def _cleanup(self, current_time: float) -> None:
        """Remove expired entries and trim cache if still too large"""
        # Remove expired entries
        expired_keys = [
            k for k, ts in self.timestamps.items()
            if current_time - ts >= self.ttl
        ]
        for k in expired_keys:
            del self.data[k]
            del self.timestamps[k]
        
        # If still too large, remove oldest entries
        if len(self.data) >= self.max_size:
            sorted_items = sorted(
                self.timestamps.items(),
                key=lambda x: x[1]
            )
            to_remove = len(self.data) - (self.max_size * 0.8)  # Remove 20% of entries
            for k, _ in sorted_items[:int(to_remove)]:
                del self.data[k]
                del self.timestamps[k]

class RateLimiter:
    """Token bucket rate limiter"""
    def __init__(self, rate: float, capacity: int):
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.last_update = time.time()
        self.lock = asyncio.Lock()
    
    async def acquire(self):
        async with self.lock:
            now = time.time()
            time_passed = now - self.last_update
            self.tokens = min(
                self.capacity,
                self.tokens + time_passed * self.rate
            )
            self.last_update = now
            
            if self.tokens < 1:
                wait_time = (1 - self.tokens) / self.rate
                await asyncio.sleep(wait_time)
                self.tokens = 0
                self.last_update = time.time()
            else:
                self.tokens -= 1

async def retry_with_backoff(func, *args, max_retries=MAX_RETRIES, initial_delay=RETRY_DELAY):
    """Retry a function with exponential backoff."""
    delay = initial_delay
    last_exception = None
    
    for attempt in range(max_retries):
        try:
            return await func(*args)
        except Exception as e:
            last_exception = e
            if "429" in str(e):  # Rate limit error
                logger.warning(f"Rate limit hit, retrying in {delay} seconds...")
                await asyncio.sleep(delay)
                delay *= 2  # Exponential backoff
            else:
                raise e
    
    logger.error(f"Max retries ({max_retries}) reached. Last error: {last_exception}")
    raise last_exception

class WalletTracker:
    def __init__(self):
        # Load environment variables
        load_dotenv()
        
        self.etherscan_api_key = os.getenv('ETHERSCAN_API_KEY')
        
        if not self.etherscan_api_key:
            raise ValueError("Etherscan API key not found in .env file")
            
        logger.info("Initializing WalletTracker with Etherscan API...")
        
        # Initialize Web3 with public endpoint
        self.w3 = Web3(Web3.HTTPProvider('https://cloudflare-eth.com'))
        if not self.w3.is_connected():
            raise ConnectionError("Failed to connect to Ethereum network")
        
        self.chex_contract = '0x9Ce84F6A69986a83d92C324df10bC8E64771030f'
        self.doge_contract = '0x4206931337dc273a630d328dA6441786BfaD668f'
        
        # Enhanced caching system
        self._wallet_analysis_cache = TransactionCache()
        self._transfer_cache = TransactionCache()
        self._balance_cache = TransactionCache()
        
        # Rate limiter for Etherscan
        self.etherscan_limiter = RateLimiter(ETHERSCAN_RATE_LIMIT, 10)
        
        # Connection pools
        self.session = self._initialize_session()
        self.thread_pool = ThreadPoolExecutor(max_workers=MAX_WORKERS)
        
        # Request semaphore for concurrent API calls
        self.request_semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        
        # Initialize address mappings
        self._initialize_address_mappings()
        
        self.balance_history_file = 'token_balance_history.json'
        self.load_balance_history()
        
        # Batch processing queue
        self.api_batch_queue = deque(maxlen=1000)

    def _initialize_session(self) -> aiohttp.ClientSession:
        """Initialize aiohttp session with connection pooling"""
        connector = aiohttp.TCPConnector(
            limit=100,  # Maximum number of concurrent connections
            ttl_dns_cache=300,  # DNS cache TTL
            use_dns_cache=True,
            limit_per_host=10  # Maximum concurrent connections per host
        )
        timeout = aiohttp.ClientTimeout(total=30)
        return aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers={"Content-Type": "application/json"}
        )

    def _initialize_address_mappings(self):
        """Initialize address mappings with validation"""
        # Known exchange addresses and labels
        self.known_addresses = {
            to_checksum_address(k): v for k, v in {
                '0x28C6c06298d514Db089934071355E5743bf21d60': 'Binance',
                '0x21a31Ee1afC51d94C2eFcCAa2092aD1028285549': 'Binance Cold Wallet',
                '0xdFd5293D8e347dFe59E90eFd55b2956a1343963d': 'Bitfinex',
                '0x1151314c646Ce4E0eFD76d1aF4760aE66a9Fe30F': 'Kraken',
            }.items()
        }
        
        # Initialize exchange addresses with validation
        self.doge_addresses = {
            name: to_checksum_address(addr)
            for name, addr in {
                'Binance': '0x28C6c06298d514Db089934071355E5743bf21d60',
                'Bitfinex': '0x77134cbC06cB00b66F4c7e623D5fdBF6777635EC',
                'Kraken': '0x2B5634C42055806a59e9107ED44D43c426E58258',
                'Binance Hot Wallet': '0x8894E0a0c962CB723c1976a4421c95949bE2D4E3',
                'Crypto.com': '0x6262998Ced04146fA42253a5C0AF90CA02dfd2A3'
            }.items()
        }

    async def get_gas_price(self) -> dict:
        """Get current gas prices from Etherscan API"""
        endpoint = 'https://api.etherscan.io/api'
        params = {
            'module': 'gastracker',
            'action': 'gasoracle',
            'apikey': self.etherscan_api_key
        }
        
        async with self.session.get(endpoint, params=params) as response:
            data = await response.json()
            if data['status'] == '1':
                result = data['result']
                return {
                    'SafeLow': result['SafeGasPrice'],
                    'Standard': result['ProposeGasPrice'],
                    'Fast': result['FastGasPrice']
                }
            else:
                raise Exception(f"Failed to get gas price: {data.get('message')}")

    async def get_latest_block(self) -> int:
        """Get latest block number from Etherscan API"""
        endpoint = 'https://api.etherscan.io/api'
        params = {
            'module': 'proxy',
            'action': 'eth_blockNumber',
            'apikey': self.etherscan_api_key
        }
        
        async with self.session.get(endpoint, params=params) as response:
            data = await response.json()
            if data['status'] == '1':
                return int(data['result'], 16)
            else:
                raise Exception(f"Failed to get latest block: {data.get('message')}")

    async def identify_wallet_cluster(self, address: str) -> str:
        """Cached wallet cluster identification with batch processing support"""
        # Check cache
        cache_key = f"cluster_{address}"
        cached_result = self._wallet_analysis_cache.get(cache_key)
        if cached_result:
            return cached_result
            
        try:
            checksum_address = to_checksum_address(address)
            
            if checksum_address in self.known_addresses:
                result = self.known_addresses[checksum_address]
                self._wallet_analysis_cache.set(cache_key, result)
                return result
            
            # Add to batch queue for processing
            self.api_batch_queue.append(('code', checksum_address))
            self.api_batch_queue.append(('txcount', checksum_address))
            
            # Process batch if queue is full
            if len(self.api_batch_queue) >= CHUNK_SIZE:
                await self._process_api_batch()
            
            code = await self.get_contract_code(checksum_address)
            
            if code != '':
                tx_count = await self.get_transaction_count(checksum_address)
                result = "Possible Exchange (High Activity)" if tx_count > 100000 else "Smart Contract"
                self._wallet_analysis_cache.set(cache_key, result)
                return result
            
            result = "Unknown Wallet"
            self._wallet_analysis_cache.set(cache_key, result)
            return result
        except Exception as e:
            logger.error(f"Error identifying wallet cluster for {address}: {str(e)}")
            return "Unknown"

    async def get_contract_code(self, address: str) -> str:
        """Get contract code using Etherscan API"""
        endpoint = 'https://api.etherscan.io/api'
        params = {
            'module': 'contract',
            'action': 'getabi',
            'address': address,
            'apikey': self.etherscan_api_key
        }
        
        try:
            async with self.session.get(endpoint, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get('status') == '1' and data.get('result'):
                        return data['result']
                    elif 'Max rate limit reached' in str(data.get('result', '')):
                        await asyncio.sleep(1)  # Wait before retry
                        return ''
                return ''
        except Exception as e:
            logger.error(f"Error getting contract code for {address}: {str(e)}")
            return ''

    async def get_transaction_count(self, address: str) -> int:
        """Get transaction count using Etherscan API"""
        endpoint = 'https://api.etherscan.io/api'
        params = {
            'module': 'proxy',
            'action': 'eth_getTransactionCount',
            'address': address,
            'tag': 'latest',
            'apikey': self.etherscan_api_key
        }
        
        try:
            async with self.session.get(endpoint, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get('result'):
                        return int(data['result'], 16)
                    elif 'Max rate limit reached' in str(data.get('result', '')):
                        await asyncio.sleep(1)  # Wait before retry
                        return 0
                return 0
        except Exception as e:
            logger.error(f"Error getting transaction count for {address}: {str(e)}")
            return 0

    async def get_token_balance(self, token_address: str, wallet_address: str) -> float:
        """Get token balance using Etherscan API"""
        endpoint = 'https://api.etherscan.io/api'
        params = {
            'module': 'account',
            'action': 'tokenbalance',
            'contractaddress': token_address,
            'address': wallet_address,
            'tag': 'latest',
            'apikey': self.etherscan_api_key
        }
        
        async with self.session.get(endpoint, params=params) as response:
            data = await response.json()
            if data['status'] == '1':
                # Convert from wei to token units (assuming 18 decimals)
                balance_wei = int(data['result'])
                return balance_wei / 1e18
            return 0.0

    async def _process_api_batch(self) -> None:
        """Process batched API requests with rate limiting"""
        if not self.api_batch_queue:
            return
        
        batch = list(islice(self.api_batch_queue, CHUNK_SIZE))
        self.api_batch_queue.clear()
        
        async def process_request(req_type: str, address: str) -> Tuple[str, Any]:
            try:
                await self.etherscan_limiter.acquire()
                if req_type == 'code':
                    result = await self.get_contract_code(address)
                elif req_type == 'txcount':
                    result = await self.get_transaction_count(address)
                else:
                    result = None
                return address, result
            except Exception as e:
                logger.error(f"Error in batch API request for {address}: {str(e)}")
                return address, None
        
        tasks = []
        for req_type, addr in batch:
            task = process_request(req_type, addr)
            tasks.append(task)
            # Add delay between requests to respect rate limit
            await asyncio.sleep(0.2)
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Update caches with results
        for result in results:
            if isinstance(result, tuple) and result[1] is not None:
                address, data = result
                cache_key = f"cluster_{address}"
                if data:  # If we got valid data
                    self._wallet_analysis_cache.set(cache_key, data)

    async def get_token_transfers_async(self, token_type: str, hours: int = 24) -> List[dict]:
        """Asynchronous token transfer fetching with enhanced caching and rate limiting"""
        cache_key = f"{token_type}_{hours}"
        cached_result = self._transfer_cache.get(cache_key)
        if cached_result:
            logger.info(f"Found {len(cached_result)} cached {token_type} transfers")
            return cached_result
        
        endpoint = 'https://api.etherscan.io/api'
        
        current_time = datetime.now()
        start_time = int((current_time - timedelta(hours=hours)).timestamp())
        
        contract_address = {
            'CHEX': self.chex_contract,
            'DOGE': self.doge_contract
        }.get(token_type)
        
        if not contract_address:
            logger.error(f"Invalid token type: {token_type}")
            return []
        
        params = {
            'module': 'account',
            'action': 'tokentx',
            'contractaddress': contract_address,
            'starttime': start_time,
            'sort': 'desc',
            'apikey': self.etherscan_api_key
        }
        
        logger.info(f"Fetching {token_type} transfers since {datetime.fromtimestamp(start_time)}")
        
        max_retries = 3
        retry_delay = 1
        
        for attempt in range(max_retries):
            try:
                async with self.request_semaphore:
                    await self.etherscan_limiter.acquire()
                    
                    async with self.session.get(endpoint, params=params, timeout=30) as response:
                        if response.status == 200:
                            data = await response.json()
                            if data['status'] == '1':
                                result = data['result']
                                logger.info(f"Successfully fetched {len(result)} {token_type} transfers")
                                self._transfer_cache.set(cache_key, result)
                                return result
                            elif 'Max rate limit reached' in str(data.get('result', '')):
                                logger.warning(f"Rate limit hit for {token_type}, attempt {attempt + 1}/{max_retries}")
                                await asyncio.sleep(retry_delay * (2 ** attempt))  # Exponential backoff
                                continue
                        
                        logger.error(f"API error for {token_type}: {await response.text()}")
                        return []
                        
            except Exception as e:
                logger.error(f"Error fetching {token_type} transfers: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay * (2 ** attempt))
                else:
                    return []
        
        return []

    async def get_token_balances_async(self, token_type: str, addresses: Dict[str, str]) -> Dict[str, float]:
        """Fetch token balances asynchronously with batch processing"""
        balances = {}
        contract_address = {
            'DOGE': self.doge_contract
        }.get(token_type)
        
        if not contract_address:
            return balances
        
        # Split addresses into chunks for batch processing
        address_chunks = [
            list(chunk)
            for chunk in self._chunks(addresses.items(), CHUNK_SIZE)
        ]
        
        async def process_chunk(chunk: List[Tuple[str, str]]) -> List[Tuple[str, float]]:
            async def get_balance(name: str, address: str) -> Tuple[str, float]:
                cache_key = f"{token_type}_{address}"
                cached_balance = self._balance_cache.get(cache_key)
                if cached_balance is not None:
                    return name, cached_balance
                
                try:
                    await self.etherscan_limiter.acquire()
                    endpoint = 'https://api.etherscan.io/api'
                    params = {
                        'module': 'account',
                        'action': 'tokenbalance',
                        'contractaddress': contract_address,
                        'address': address,
                        'tag': 'latest',
                        'apikey': self.etherscan_api_key
                    }
                    
                    async with self.session.get(endpoint, params=params) as response:
                        data = await response.json()
                        if data['status'] == '1':
                            balance_wei = int(data['result'])
                            balance_eth = from_wei(balance_wei, 'ether')
                            self._balance_cache.set(cache_key, balance_eth)
                            return name, balance_eth
                        return name, 0.0
                except Exception as e:
                    logger.error(f"Error getting balance for {name}: {e}")
                    return name, 0.0
            
            tasks = [get_balance(name, addr) for name, addr in chunk]
            return await asyncio.gather(*tasks)
        
        # Process chunks concurrently
        chunk_results = await asyncio.gather(*[
            process_chunk(chunk) for chunk in address_chunks
        ])
        
        # Combine results
        for chunk_result in chunk_results:
            balances.update(dict(chunk_result))
        
        return balances

    @staticmethod
    def _chunks(data, n):
        """Yield successive n-sized chunks from any iterable."""
        data = list(data)  # Convert to list for slicing
        for i in range(0, len(data), n):
            yield data[i:i + n]

    async def analyze_wallet_movement_async(self, address: str, hours: int = 24) -> dict:
        """Asynchronous wallet movement analysis with enhanced caching"""
        # Check cache
        cache_key = f"{address}_{hours}"
        cached_result = self._wallet_analysis_cache.get(cache_key)
        if cached_result:
            return cached_result
        
        # Get transaction sequence
        tx_sequence = await self.analyze_transaction_sequence_async(address, hours)
        
        # Identify wallet cluster (already cached)
        cluster_type = await self.identify_wallet_cluster(address)
        
        # Analyze gas patterns
        unusual_gas = self.detect_unusual_gas_patterns(tx_sequence)
        
        # Calculate basic statistics
        if tx_sequence:
            total_in = sum(tx['amount'] for tx in tx_sequence if tx['type'] == 'in')
            total_out = sum(tx['amount'] for tx in tx_sequence if tx['type'] == 'out')
            total_gas = sum(tx['gas_price'] * tx['gas_used'] for tx in tx_sequence)
            
            result = {
                'address': address,
                'cluster_type': cluster_type,
                'total_incoming': total_in,
                'total_outgoing': total_out,
                'transaction_count': len(tx_sequence),
                'total_gas_spent': from_wei(total_gas, 'ether'),
                'unusual_patterns': unusual_gas,
                'transaction_sequence': tx_sequence
            }
            
            # Update cache
            self._wallet_analysis_cache.set(cache_key, result)
            
            return result
        
        return None

    async def analyze_transaction_sequence_async(self, address: str, hours: int = 24) -> List[dict]:
        """Analyze transaction sequences asynchronously with rate limiting"""
        async with self.request_semaphore:
            await self.etherscan_limiter.acquire()
            
            endpoint = 'https://api.etherscan.io/api'
            
            params = {
                'module': 'account',
                'action': 'tokentx',
                'address': address,
                'startblock': 0,
                'endblock': 99999999,
                'sort': 'desc',
                'apikey': self.etherscan_api_key
            }
            
            try:
                async with self.session.get(endpoint, params=params) as response:
                    if response.status != 200:
                        return []
                    
                    data = await response.json()
                    if data['status'] != '1':
                        return []
                    
                    transactions = data['result']
                    logger.info(f"Analyzing {len(transactions)} transactions for address {address[:8]}...")
                    
                    # Process transactions
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
                    
                    if len(timeline) > 0:
                        logger.info(f"Found {len(timeline)} relevant transactions for address {address[:8]}")
                    
                    return timeline
            except Exception as e:
                logger.error(f"Error analyzing transaction sequence: {e}")
                return []

    def detect_unusual_gas_patterns(self, transactions):
        """Simplified detection of unusual gas patterns"""
        if not transactions:
            return []
        
        # Sort by gas price for quick percentile calculation
        sorted_gas = sorted([tx['gas_price'] for tx in transactions])
        high_gas_threshold = sorted_gas[int(len(sorted_gas) * GAS_PERCENTILE_THRESHOLD / 100)]
        
        unusual_patterns = []
        last_tx_time = None
        
        for tx in transactions:
            current_time = tx['timestamp']
            
            # Check for high gas price (above 95th percentile)
            if tx['gas_price'] > high_gas_threshold:
                unusual_patterns.append({
                    'type': 'High Gas Price',
                    'timestamp': current_time,
                    'gas_price': tx['gas_price']
                })
            
            # Check for rapid transactions (within 30 seconds)
            if last_tx_time and (last_tx_time - current_time).total_seconds() < 30:
                unusual_patterns.append({
                    'type': 'Rapid Transactions',
                    'timestamp': current_time,
                    'time_difference': (last_tx_time - current_time).total_seconds()
                })
            
            last_tx_time = current_time
        
        return unusual_patterns

    def create_transaction_dataframe(self, transactions: List[dict]) -> pd.DataFrame:
        """Convert transaction data to pandas DataFrame for analysis"""
        if not transactions:
            return pd.DataFrame()
        
        # Create DataFrame from raw transactions
        df = pd.DataFrame(transactions)
        
        # Debug log for first few transactions
        if len(transactions) > 0:
            token_symbol = transactions[0].get('tokenSymbol', 'Unknown')
            logger.info(f"\nAnalyzing {token_symbol} transactions:")
            for i, tx in enumerate(transactions[:3]):
                logger.info(f"Transaction {i + 1}:")
                logger.info(f"  Value (wei): {tx.get('value', '0')}")
                logger.info(f"  From: {tx.get('from', 'Unknown')}")
                logger.info(f"  To: {tx.get('to', 'Unknown')}")
                logger.info(f"  Token Decimals: {tx.get('tokenDecimal', '18')}")
        
        # Convert timeStamp to datetime
        df['timestamp'] = pd.to_datetime(df['timeStamp'].astype(int), unit='s')
        df['hour'] = df['timestamp'].dt.hour
        df['day_of_week'] = df['timestamp'].dt.day_name()
        
        # Convert value to numeric and from wei to ether using token decimals
        def convert_to_token_amount(row):
            value = int(row['value'])
            decimals = int(row.get('tokenDecimal', 18))  # Default to 18 if not specified
            return float(value) / (10 ** decimals)
        
        df['amount'] = df.apply(convert_to_token_amount, axis=1)
        
        # Convert gas values to numeric using string conversion for large numbers
        df['gas_price'] = df['gasPrice'].apply(lambda x: int(x))
        df['gas_used'] = df['gasUsed'].apply(lambda x: int(x))
        df['gas_cost'] = df['gas_price'] * df['gas_used']
        
        # Log summary statistics
        if not df.empty:
            logger.info(f"\nSummary statistics for {token_symbol}:")
            logger.info(f"Total transactions: {len(df)}")
            logger.info(f"Total amount: {df['amount'].sum():,.2f}")
            logger.info(f"Average amount: {df['amount'].mean():,.2f}")
        
        return df

    def analyze_transaction_patterns(self, df: pd.DataFrame) -> dict:
        """Analyze transaction patterns using pandas"""
        if df.empty:
            return {}
        
        analysis = {
            'hourly_volume': df.groupby('hour')['amount'].sum().to_dict(),
            'daily_patterns': df.groupby('day_of_week')['amount'].agg(['count', 'sum', 'mean']).to_dict(),
            'gas_statistics': {
                'mean': float(df['gas_cost'].mean()),
                'median': float(df['gas_cost'].median()),
                'std': float(df['gas_cost'].std())
            }
        }
        
        # Add percentile analysis
        percentiles = {}
        for p in [25, 50, 75, 90, 95]:
            percentiles[f'p{p}'] = float(df['amount'].quantile(p/100))
        analysis['amount_percentiles'] = percentiles
        
        return analysis

    def create_transaction_network(self, transactions: List[dict]) -> nx.DiGraph:
        """Create a directed graph of transactions for network analysis"""
        G = nx.DiGraph()
        
        for tx in transactions:
            from_addr = tx['from']
            to_addr = tx['to']
            amount = float(self.w3.from_wei(int(tx['value']), 'ether'))
            
            if from_addr and to_addr:
                if G.has_edge(from_addr, to_addr):
                    # Update existing edge
                    G[from_addr][to_addr]['weight'] += amount
                    G[from_addr][to_addr]['count'] += 1
                else:
                    # Create new edge
                    G.add_edge(from_addr, to_addr, weight=amount, count=1)
        
        return G

    def analyze_network_metrics(self, df):
        """Analyze network metrics from transaction data."""
        G = nx.DiGraph()
        
        # Add edges with weights
        for _, row in df.iterrows():
            G.add_edge(row['from'], row['to'], weight=float(row['amount']))
        
        # Calculate basic network metrics
        metrics = {
            'total_nodes': G.number_of_nodes(),
            'total_edges': G.number_of_edges(),
            'density': nx.density(G),
            'largest_component_size': len(max(nx.weakly_connected_components(G), key=len))
        }
        
        # Calculate average clustering if there are enough nodes
        if G.number_of_nodes() > 2:
            metrics['avg_clustering'] = nx.average_clustering(G.to_undirected())
        else:
            metrics['avg_clustering'] = 0
        
        # Calculate weighted in and out degrees
        weighted_in_degrees = {node: sum(d['weight'] for (u, v, d) in G.in_edges(node, data=True)) 
                             for node in G.nodes()}
        weighted_out_degrees = {node: sum(d['weight'] for (u, v, d) in G.out_edges(node, data=True)) 
                              for node in G.nodes()}
        
        # Get top senders and receivers based on weighted degrees
        top_senders = sorted(weighted_out_degrees.items(), key=lambda x: x[1], reverse=True)[:5]
        top_receivers = sorted(weighted_in_degrees.items(), key=lambda x: x[1], reverse=True)[:5]
        
        metrics['top_senders'] = [(addr, amount) for addr, amount in top_senders]
        metrics['top_receivers'] = [(addr, amount) for addr, amount in top_receivers]
        
        return metrics

    async def generate_enhanced_report_async(self, hours: int = 1, quick_mode: bool = False) -> str:
        """Generate an enhanced report with transaction patterns and network analysis."""
        try:
            logger.info(f"\nStarting enhanced analysis for the last {hours} hours (Quick Mode: {quick_mode})...")
            
            # Process CHEX transfers
            logger.info(f"Fetching CHEX transfers since {datetime.now() - timedelta(hours=hours)}")
            chex_transfers = await self.get_token_transfers_async('CHEX', hours)
            logger.info(f"Successfully fetched {len(chex_transfers)} CHEX transfers")
            
            # Add delay to avoid rate limits
            await asyncio.sleep(1)
            
            # Process DOGE transfers
            logger.info(f"Fetching DOGE transfers since {datetime.now() - timedelta(hours=hours)}")
            doge_transfers = await self.get_token_transfers_async('DOGE', hours)
            logger.info(f"Successfully fetched {len(doge_transfers)} DOGE transfers")
            
            total_transfers = len(chex_transfers) + len(doge_transfers)
            logger.info(f"Processing {total_transfers} total transfers")
            
            report = f"""Enhanced Wallet Movement Report - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Analysis Period: Last {hours} hours
Analysis Mode: {'Quick' if quick_mode else 'Full'}\n"""
            
            # Process each token type
            for token, transfers in [('CHEX', chex_transfers), ('DOGE', doge_transfers)]:
                try:
                    # Create DataFrame
                    df = self.create_transaction_dataframe(transfers)
                    
                    # Analyze patterns
                    patterns = self.analyze_transaction_patterns(df)
                    
                    # Create network and analyze metrics
                    metrics = self.analyze_network_metrics(df)
                    
                    # Add token section to report
                    report += f"\n=== {token} Analysis ===\n"
                    report += f"Total Transactions: {len(df):,}\n"
                    report += f"Total Volume: {df['amount'].sum():,.2f} {token}\n"
                    report += f"Average Transaction: {df['amount'].mean():,.2f} {token}\n"
                    
                    # Add transaction size distribution
                    report += "\nTransaction Size Distribution:\n"
                    for percentile, value in patterns['amount_percentiles'].items():
                        report += f"{percentile} percentile: {value:,.2f} {token}\n"
                    
                    # Add hourly activity
                    report += "\nHourly Activity (Top 5):\n"
                    for hour, volume in patterns['hourly_volume'].items():
                        report += f"Hour {hour:02d}:00 - {volume:,.2f} {token}\n"
                    
                    # Add gas statistics
                    report += "\nGas Usage Statistics:\n"
                    for stat, value in patterns['gas_statistics'].items():
                        report += f"{stat}: {value:,.2f} wei\n"
                    
                    # Add network metrics
                    report += "\nNetwork Analysis:\n"
                    report += f"Active Addresses: {metrics['total_nodes']}\n"
                    report += f"Unique Transfers: {metrics['total_edges']}\n"
                    report += f"Network Density: {metrics['density']:.4f}\n"
                    report += f"Largest Component Size: {metrics['largest_component_size']}\n"
                    
                    # Add top senders and receivers
                    report += "\nTop Senders:\n"
                    for addr, amount in metrics['top_senders']:
                        label = self.known_addresses.get(addr, 'Unknown Wallet')
                        report += f"- {addr[:8]}... ({label}): {amount:,.2f} {token}\n"
                    
                    report += "\nTop Receivers:\n"
                    for addr, amount in metrics['top_receivers']:
                        label = self.known_addresses.get(addr, 'Unknown Wallet')
                        report += f"- {addr[:8]}... ({label}): {amount:,.2f} {token}\n"
                    
                except Exception as e:
                    logger.error(f"Error processing {token} data: {e}")
                    report += f"\nError processing {token} data: {e}\n"
            
            report += "\n" + "-" * 80 + "\n"
            
            # Save report to file
            filename = f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            with open(filename, 'w') as f:
                f.write(report)
            logger.info(f"\nReport saved to {filename}")
            
            return report
        
        except Exception as e:
            error_msg = f"Error generating report: {e}"
            logger.error(error_msg)
            return error_msg

    async def close(self):
        """Clean up resources properly"""
        try:
            if hasattr(self, 'session') and not self.session.closed:
                await self.session.close()
                
            if hasattr(self, 'thread_pool'):
                self.thread_pool.shutdown(wait=True)
                
            # Wait for any remaining tasks
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
                
        except Exception as e:
            logger.error(f"Error during cleanup: {e}", exc_info=True)

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

async def main():
    tracker = None
    try:
        tracker = WalletTracker()
        
        # Generate report with quick mode for faster results
        report = await tracker.generate_enhanced_report_async(
            hours=1,  # Analyze last 1 hour of transactions
            quick_mode=True  # Enable quick mode by default
        )
        
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
        logger.error(f"Error running wallet tracker: {e}", exc_info=True)
    finally:
        if tracker:
            await tracker.close()

if __name__ == "__main__":
    asyncio.run(main()) 