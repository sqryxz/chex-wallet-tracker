# Large Wallet Movement Aggregator

This tool tracks large wallet movements for CHEX (ERC-20), DOGE (Wrapped), and AAVE cryptocurrencies, generating daily reports of significant transfers. It helps monitor whale movements, exchange activities, and unusual transaction patterns across these tokens.

## Features

- Tracks CHEX token transfers using Etherscan API
- Tracks DOGE (Wrapped) token transfers using Etherscan API
- Tracks AAVE token transfers using Etherscan API
- Identifies wallet clusters (exchanges, smart contracts, etc.)
- Detects unusual gas patterns and rapid transactions
- Analyzes transaction sequences for patterns
- Maintains historical balance tracking
- Generates comprehensive daily reports
- Configurable minimum amount thresholds
- Saves reports to text files

## Requirements

- Python 3.8+
- Ethereum node access (via Infura)
- Etherscan API key
- Required Python packages (see requirements.txt)

## Setup

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd large-wallet-movement-tool
   ```

2. Install dependencies:
   ```bash
   python3 -m pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```

4. Edit `.env` and add your API keys:
   ```
   ETHERSCAN_API_KEY=your_etherscan_key_here
   INFURA_API_KEY=your_infura_key_here
   ```
   - Get an Etherscan API key from https://etherscan.io/apis
   - Get an Infura API key from https://infura.io

## Usage

Run the script:
```bash
python3 wallet_tracker.py
```

The script will:
1. Connect to Ethereum network via Infura
2. Fetch the last 24 hours of transactions
3. Filter for large movements (default thresholds):
   - 10,000+ CHEX
   - 50,000+ DOGE
   - 100+ AAVE
4. Generate a report with statistics and top movements
5. Save the report to a dated file (e.g., `report_20240315.txt`)

### Report Contents

The generated report includes:

1. Wallet Clustering Analysis:
   - Address identification
   - Cluster type (Exchange, Smart Contract, Unknown)
   - Total incoming/outgoing amounts
   - Transaction counts
   - Gas usage statistics

2. Unusual Activity Patterns:
   - High gas price spikes
   - Rapid successive transactions
   - Large balance changes
   - Exchange deposit/withdrawal patterns

## Configuration

You can modify the following parameters in `wallet_tracker.py`:

### Token Tracking
- Token contract addresses
- Exchange wallet addresses
- Minimum amount thresholds in `get_token_movements()`
- Time window (default 24 hours) in `get_token_transfers()` and `get_token_movements()`

### Analysis Parameters
- Gas price analysis thresholds
- Transaction sequence analysis window
- Wallet clustering parameters
- Historical data retention period (default 7 days)

## Data Storage

- Transaction reports are saved as text files with timestamps
- Historical balance data is stored in `token_balance_history.json`
- Each report contains a full analysis of the tracked period

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the LICENSE file for details. 