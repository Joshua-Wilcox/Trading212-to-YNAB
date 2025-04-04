# Trading 212 to YNAB Sync

This tool helps you sync transactions from Trading 212 to YNAB (You Need A Budget). It allows you to filter specific transaction types (like cashback or interest) before sending them to YNAB.

## Setup

1. **Install Required Packages**

   ```bash
   pip install requests python-dotenv
   ```

2. **Environment Variables**

   Create a `.env` file in the root directory with the following variables:

   ```
   YNAB_TOKEN=your_ynab_api_token
   BUDGET=your_ynab_budget_id
   ACCOUNT=your_ynab_account_id
   TRADING212_TOKEN=your_trading212_api_token  # Only needed for automatic fetching
   ```

   - YNAB_TOKEN: Your YNAB API token (get it from https://app.youneedabudget.com/settings/developer)
   - BUDGET: Your YNAB budget ID (found in the URL when viewing your budget)
   - ACCOUNT: The YNAB account ID where transactions should be added
   - TRADING212_TOKEN: Your Trading 212 API token (needed only if using the --fetch option)

   **Important note about Trading 212 API tokens**: When adding your Trading 212 token to the .env file, do not include "Bearer " prefix - the script adds this automatically.

3. **Getting Transaction Data**

   There are two ways to get your Trading 212 transaction data:

   **Option 1: Download CSV manually**
   - Log into your Trading 212 account
   - Go to the History section
   - Export your transaction history as CSV
   - Save the CSV file locally

   **Option 2: Automatic fetch using the Trading 212 API**
   - Get an API token from Trading 212
   - Add it to your .env file as TRADING212_TOKEN
   - Use the --fetch option when running the script

## Usage

### Basic Usage with CSV file

```bash
python main.py --csv path/to/your/trading212_export.csv
```

### Basic Usage with automatic fetch

```bash
python main.py --fetch
```

If you're using a Trading 212 demo account, add the --demo flag:

```bash
python main.py --fetch --demo
```

### Filtering Transaction Types

Only process certain transaction types:

```bash
python main.py --fetch --filter "Interest on cash" "Lending interest"
```

Available transaction types:
- "Deposit"
- "Withdrawal"
- "Market buy"
- "Market sell"
- "Dividend (Dividend)"
- "Interest on cash"
- "Lending interest"
- "Currency conversion"
- "New card cost"
- "Cashback"
- "Card debit"
- "Card credit"
- "Spending cashback"

### Save Transactions to JSON

Save the processed transactions to a JSON file:

```bash
python main.py --fetch --output transactions.json
```

### Send to YNAB

Process transactions and send them to YNAB:

```bash
python main.py --fetch --filter "Interest on cash" --send
```

### Complete Example

Process only interest and cashback transactions, save them to JSON, and send to YNAB:

```bash
python main.py --fetch --filter "Interest on cash" "Cashback" --output transactions.json --send
```

### Specifying Date Ranges

By default, the script fetches transactions from the last year. You can customize this with two options:

**Option 1: Specify number of days**

Fetch transactions from the last N days:

```bash
python main.py --fetch --days 7 --send
```

**Option 2: Specify start date**

Fetch transactions starting from a specific date (DD/MM/YYYY format):

```bash
python main.py --fetch --start-date 01/01/2023 --send
```

Both options will start at the beginning of the specified day (00:00 UTC) and end at the current time.

## Enhanced Transaction Details

The script now supports enhanced transaction details for YNAB:

1. For card transactions, merchant names are used as payee names
2. Merchant categories are included in the transaction memo
3. Stock transactions include ticker symbol, share count, and price per share
4. Refunds and payouts are properly labeled

This makes your YNAB transactions more descriptive and easier to categorize.

## Automated Running

This script is designed to be safe for automated running (e.g., via cron job or scheduled task). It relies on YNAB's built-in deduplication mechanism to prevent duplicate transactions.

YNAB uses the `import_id` field to identify duplicate transactions. The script generates a unique import_id for each transaction based on its details, ensuring that even if the same transaction is sent multiple times, it will only be imported once in YNAB.

To set up automated running:

### On Unix/Linux/macOS (using cron):

```bash
# Run every hour
0 * * * * cd /path/to/T212-YNAB && python main.py --fetch --filter "Interest on cash" "Cashback" "Card debit" --send
```

For daily syncing of only the last day's transactions:

```bash
# Run daily at midnight to sync the previous day's transactions
0 0 * * * cd /path/to/T212-YNAB && python main.py --fetch --days 1 --send
```

### On Windows (using Task Scheduler):

1. Create a batch file (e.g., `sync_t212_ynab.bat`) with:
   ```
   cd C:\path\to\T212-YNAB
   python main.py --fetch --filter "Interest on cash" "Cashback" "Card debit" --send
   ```
2. Create a new task in Task Scheduler that runs this batch file hourly

If you need to disable the cache for some reason (e.g., to resend all transactions):

```bash
python main.py --fetch --send --no-cache
```

You can also specify a custom location for the cache file:

```bash
python main.py --fetch --send --cache-file /path/to/custom_cache.json
```

### Debugging Options

To help with troubleshooting issues like transaction amount discrepancies, you can save the raw CSV file before any processing:

```bash
python main.py --fetch --save-raw-csv raw_transactions.csv
```

This will save the raw CSV content exactly as received from Trading 212 (either from API or input file), allowing you to inspect the original data format and values. This is especially useful when diagnosing issues with the money amount parsing.

## Troubleshooting

If you encounter a 429 "Too Many Requests" error:
1. The Trading 212 API has strict rate limits (1 request per minute for exports list, 1 request per 30 seconds for creating exports)
2. The script now includes automatic rate limiting and retry logic
3. If you still face issues, wait at least 1 minute before trying again
4. Consider using the CSV download method if rate limiting persists

If you encounter a 401 Unauthorized error when using --fetch, check that:
1. Your Trading 212 API token is correct and has not expired
2. The token has the proper permissions for accessing history data
3. You're using the right environment (--demo flag if it's a demo account)
4. The token is added to your .env file without the "Bearer " prefix

### Handling Duplicate Transactions

If you delete transactions in YNAB but they still show as duplicates when re-importing:

1. YNAB remembers previously imported transactions by their import_id, even after deletion
2. You can force new import IDs by using the `--id-version` flag:

```bash
python main.py --fetch --id-version 16 --send
```

Each time you need to reimport previously deleted transactions, just increment the version number.

## How It Works

1. The script either loads a local CSV file or fetches data directly from the Trading 212 API
2. It converts the transactions to a JSON format
3. It filters transactions based on your specified transaction types
4. It can save these transactions to a JSON file for your records
5. It can send the transactions to YNAB using the YNAB API

## Notes

- The script generates import IDs based on transaction details to prevent duplicate imports
- Transactions are marked as "cleared" in YNAB
- Make sure your YNAB account currency matches your Trading 212 account currency
- When using --fetch, the script retrieves transactions from the past year
- The Trading 212 API has rate limits that the script automatically respects
