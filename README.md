# Large Wallet Movement Aggregator

This tool tracks large wallet movements for CHEX (ERC-20) and XRP cryptocurrencies, generating daily reports of significant transfers.

## Features

- Tracks CHEX token transfers using Etherscan API
- Tracks XRP movements using XRPScan API
- Generates daily reports of large movements
- Configurable minimum amount thresholds
- Saves reports to text files

## Setup

1. Clone the repository
2. Install dependencies:
   ```bash
   python3 -m pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```
4. Edit `.env` and add your API keys:
   - Get an Etherscan API key from https://etherscan.io/apis
   - Get an XRPScan API key from https://xrpscan.com/api

## Usage

Run the script:
```bash
python3 wallet_tracker.py
```

The script will:
1. Fetch the last 24 hours of transactions
2. Filter for large movements (default: 10,000+ CHEX, 100,000+ XRP)
3. Generate a report with statistics and top movements
4. Save the report to a dated file (e.g., `report_20240315.txt`)

## Configuration

You can modify the following parameters in `wallet_tracker.py`:
- Minimum amount thresholds in `generate_daily_report()`
- Time window (default 24 hours) in `get_chex_transfers()` and `get_xrp_movements()` 