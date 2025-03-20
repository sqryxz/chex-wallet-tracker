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
CHUNK_SIZE = 25  # Reduced chunk size for better rate limiting
MAX_WORKERS = 5  # Reduced worker count
CACHE_TTL = 3600  # Cache TTL in seconds
MAX_CONCURRENT_REQUESTS = 2  # Reduced concurrent requests
REQUEST_DELAY = 1.0  # Increased delay between requests
ETHERSCAN_RATE_LIMIT = 3  # Reduced requests per second for Etherscan
MAX_RETRIES = 5  # Increased maximum number of retries
RETRY_DELAY = 2  # Increased initial retry delay

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
        self.infura_api_key = os.getenv('INFURA_API_KEY')
        
        if not self.etherscan_api_key:
            raise ValueError("Etherscan API key not found in .env file")
        if not self.infura_api_key:
            raise ValueError("Infura API key not found in .env file")
            
        logger.info("Initializing WalletTracker with Etherscan API...")
        
        # Initialize Web3
        self.w3 = Web3(Web3.HTTPProvider(f'https://mainnet.infura.io/v3/{self.infura_api_key}'))
        if not self.w3.is_connected():
            raise ConnectionError("Failed to connect to Ethereum network")
        
        self.chex_contract = '0x9Ce84F6A69986a83d92C324df10bC8E64771030f'
        self.aave_contract = '0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9'
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
        
        self.aave_addresses = {
            name: to_checksum_address(addr)
            for name, addr in {
                'Binance': '0x28C6c06298d514Db089934071355E5743bf21d60',
                'Aave Treasury': '0x25F2226B597E8F9514B3F68F00f494cF4f286491',
                'Aave Collector': '0x464C71f6c2F760DdA6093dCB91C24c39e5d6e18c',
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
            return cached_result
        
        endpoint = 'https://api.etherscan.io/api'
        
        current_time = datetime.now()
        start_time = int((current_time - timedelta(hours=hours)).timestamp())
        
        contract_address = {
            'CHEX': self.chex_contract,
            'AAVE': self.aave_contract,
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
            'DOGE': self.doge_contract,
            'AAVE': self.aave_contract
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
                    
                    return timeline
            except Exception as e:
                logger.error(f"Error analyzing transaction sequence: {e}")
                return []

    async def generate_enhanced_report_async(self, hours: int = 24) -> str:
        """Generate an enhanced report asynchronously with optimized processing"""
        # Fetch token transfers concurrently with chunking
        token_transfers = await asyncio.gather(
            self.get_token_transfers_async('CHEX', hours),
            self.get_token_transfers_async('DOGE', hours),
            self.get_token_transfers_async('AAVE', hours)
        )
        
        chex_moves, doge_moves, aave_moves = token_transfers
        
        # Collect unique addresses
        analyzed_addresses = set()
        for moves in [chex_moves, doge_moves, aave_moves]:
            for tx in moves:
                analyzed_addresses.add(tx['from'])
                analyzed_addresses.add(tx['to'])
        
        # Process addresses in chunks
        address_chunks = [
            list(chunk)
            for chunk in self._chunks(analyzed_addresses, CHUNK_SIZE)
        ]
        
        detailed_analysis = []
        for chunk in address_chunks:
            # Analyze addresses in chunk concurrently
            chunk_tasks = [
                self.analyze_wallet_movement_async(address, hours)
                for address in chunk
            ]
            chunk_results = await asyncio.gather(*chunk_tasks)
            detailed_analysis.extend([r for r in chunk_results if r])
            
            # Add small delay between chunks to prevent rate limiting
            await asyncio.sleep(REQUEST_DELAY)
        
        # Generate report
        report = f"Enhanced Wallet Movement Report - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        
        # Top 10 Inflows
        report += "=== Top 10 Inflows (Last 24h) ===\n\n"
        inflows = sorted(detailed_analysis, key=lambda x: x['total_incoming'], reverse=True)[:10]
        for idx, flow in enumerate(inflows, 1):
            report += f"{idx}. Address: {flow['address']}\n"
            report += f"   Amount: {flow['total_incoming']:,.2f}\n"
            report += f"   Cluster Type: {flow['cluster_type']}\n"
            report += f"   Transaction Count: {flow['transaction_count']}\n\n"
        
        # Top 10 Outflows
        report += "=== Top 10 Outflows (Last 24h) ===\n\n"
        outflows = sorted(detailed_analysis, key=lambda x: x['total_outgoing'], reverse=True)[:10]
        for idx, flow in enumerate(outflows, 1):
            report += f"{idx}. Address: {flow['address']}\n"
            report += f"   Amount: {flow['total_outgoing']:,.2f}\n"
            report += f"   Cluster Type: {flow['cluster_type']}\n"
            report += f"   Transaction Count: {flow['transaction_count']}\n\n"
        
        # Top 10 Wallet Clusters Analysis
        report += "=== Top 10 Most Active Wallet Clusters ===\n\n"
        # Sort by total volume (in + out)
        active_wallets = sorted(
            detailed_analysis,
            key=lambda x: x['total_incoming'] + x['total_outgoing'],
            reverse=True
        )[:10]
        
        for idx, wallet in enumerate(active_wallets, 1):
            total_volume = wallet['total_incoming'] + wallet['total_outgoing']
            net_flow = wallet['total_incoming'] - wallet['total_outgoing']
            report += f"{idx}. Cluster: {wallet['cluster_type']}\n"
            report += f"   Address: {wallet['address']}\n"
            report += f"   Total Volume: {total_volume:,.2f}\n"
            report += f"   Net Flow: {net_flow:,.2f}\n"
            report += f"   Transaction Count: {wallet['transaction_count']}\n"
            report += f"   Gas Spent: {wallet['total_gas_spent']:.4f} ETH\n\n"
        
        # Gas Analysis
        report += "=== Gas Cost Analysis ===\n\n"
        
        # Calculate gas statistics
        all_gas_prices = []
        all_gas_used = []
        for analysis in detailed_analysis:
            for tx in analysis.get('transaction_sequence', []):
                all_gas_prices.append(tx['gas_price'])
                all_gas_used.append(tx['gas_used'])
        
        if all_gas_prices and all_gas_used:
            avg_gas_price = sum(all_gas_prices) / len(all_gas_prices)
            avg_gas_used = sum(all_gas_used) / len(all_gas_used)
            max_gas_price = max(all_gas_prices)
            min_gas_price = min(all_gas_prices)
            
            # Calculate percentiles for gas prices
            gas_prices_gwei = [from_wei(price, 'gwei') for price in all_gas_prices]
            percentiles = {
                '90th': pd.Series(gas_prices_gwei).quantile(0.9),
                '75th': pd.Series(gas_prices_gwei).quantile(0.75),
                '50th': pd.Series(gas_prices_gwei).quantile(0.5),
                '25th': pd.Series(gas_prices_gwei).quantile(0.25)
            }
            
            report += f"Average Gas Price: {from_wei(avg_gas_price, 'gwei'):.2f} gwei\n"
            report += f"Maximum Gas Price: {from_wei(max_gas_price, 'gwei'):.2f} gwei\n"
            report += f"Minimum Gas Price: {from_wei(min_gas_price, 'gwei'):.2f} gwei\n"
            report += f"Average Gas Used: {avg_gas_used:,.0f}\n\n"
            
            report += "Gas Price Percentiles (gwei):\n"
            for percentile, value in percentiles.items():
                report += f"{percentile}: {value:.2f}\n"
            
            # High Gas Cost Transactions
            report += "\nHigh Gas Cost Transactions (>90th percentile):\n"
            high_gas_txs = []
            for analysis in detailed_analysis:
                for tx in analysis.get('transaction_sequence', []):
                    gas_cost_gwei = from_wei(tx['gas_price'], 'gwei')
                    if gas_cost_gwei > percentiles['90th']:
                        high_gas_txs.append({
                            'address': analysis['address'],
                            'timestamp': tx['timestamp'],
                            'gas_price': gas_cost_gwei,
                            'gas_used': tx['gas_used']
                        })
            
            high_gas_txs.sort(key=lambda x: x['gas_price'], reverse=True)
            for idx, tx in enumerate(high_gas_txs[:5], 1):
                report += f"\n{idx}. Address: {tx['address']}\n"
                report += f"   Time: {tx['timestamp']}\n"
                report += f"   Gas Price: {tx['gas_price']:.2f} gwei\n"
                report += f"   Gas Used: {tx['gas_used']:,}\n"
        
        # Unusual Patterns Section
        report += "\n=== Unusual Activity Patterns ===\n"
        for analysis in detailed_analysis:
            if analysis['unusual_patterns']:
                report += f"\nAddress: {analysis['address']}\n"
                for pattern in analysis['unusual_patterns']:
                    report += f"- {pattern['type']} detected at {pattern['timestamp']}\n"
                    if 'gas_price' in pattern:
                        report += f"  Gas Price: {from_wei(pattern['gas_price'], 'gwei')} gwei\n"
                    if 'time_difference' in pattern:
                        report += f"  Time between transactions: {pattern['time_difference']:.1f} seconds\n"
        
        return report

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

async def main():
    tracker = None
    try:
        tracker = WalletTracker()
        
        # Generate enhanced report
        report = await tracker.generate_enhanced_report_async()
        
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