# CHEX Wallet Tracker

A sophisticated tool for tracking and analyzing CHEX token movements on the Ethereum blockchain. This tool provides detailed insights into wallet clustering, transaction patterns, and potential arbitrage activities.

## Features

- **Wallet Clustering**: Identifies and categorizes wallets (exchanges, smart contracts, high-activity addresses)
- **Transaction Sequencing**: Analyzes patterns in token movements and correlations between different assets
- **Gas Analysis**: Monitors unusual gas patterns that might indicate arbitrage or suspicious activity
- **Exchange Monitoring**: Tracks major exchange wallet movements
- **Comprehensive Reporting**: Generates detailed reports with wallet analysis and unusual patterns

## Prerequisites

- Python 3.8+
- Etherscan API key
- Infura API key

## Installation

1. Clone the repository:
```bash
git clone https://github.com/sqryxz/chex-wallet-tracker.git
cd chex-wallet-tracker
```

2. Install required packages:
```bash
pip install -r requirements.txt
```

3. Create a `.env` file in the project root:
```bash
ETHERSCAN_API_KEY=your_etherscan_api_key
INFURA_API_KEY=your_infura_api_key
```

## Usage

Run the tracker:
```bash
python wallet_tracker.py
```

The script will:
1. Fetch recent CHEX token transfers
2. Analyze wallet patterns and clusters
3. Detect unusual transaction sequences
4. Monitor gas usage patterns
5. Generate a comprehensive report

Reports are saved in the project directory with timestamps.

## Report Format

The generated report includes:

### Wallet Clustering Analysis
- Wallet address
- Cluster type (Exchange, Smart Contract, Unknown)
- Total CHEX inflow/outflow
- Transaction count
- Gas usage statistics

### Unusual Activity Patterns
- High gas price spikes
- Rapid successive transactions
- Correlated token movements
- Exchange wallet activities

## Configuration

Key parameters can be adjusted in `wallet_tracker.py`:
- `min_amount`: Minimum transaction size to track
- `hours`: Time window for analysis
- Transaction count thresholds for exchange detection

## Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Disclaimer

This tool is for informational purposes only. Always conduct your own research and due diligence.