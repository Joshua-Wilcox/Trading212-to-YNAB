import os
import csv
import json
import hashlib
import argparse
import datetime
import requests
import time
import random
from io import StringIO
from typing import Dict, List, Optional, Any, Union, Tuple
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Trading 212 action types based on the TypeScript enum
class Trading212Action:
    DEPOSIT = "Deposit"
    WITHDRAWAL = "Withdrawal"
    MARKET_BUY = "Market buy"
    MARKET_SELL = "Market sell"
    DIVIDEND = "Dividend (Dividend)"
    INTEREST_ON_CASH = "Interest on cash"
    LENDING_INTEREST = "Lending interest"
    CURRENCY_CONVERSION = "Currency conversion"
    NEW_CARD_COST = "New card cost"
    CASHBACK = "Cashback"
    CARD_DEBIT = "Card debit"
    CARD_CREDIT = "Card credit"
    SPENDING_CASHBACK = "Spending cashback"

# Constants
IMPORT_ID_VERSION = 15  # Increment this to generate new import IDs
IMPORT_PREFIX = "T212-"
VERSIONED_IMPORT_PREFIX = f"{IMPORT_PREFIX}v{IMPORT_ID_VERSION}:"

def parse_money(amount: str) -> int:
    """
    Parse a money string to an integer (in milliunits for YNAB).
    YNAB uses milliunits (1/1000th of the currency) rather than cents.

    Args:
        amount (str): The monetary value as a string.

    Returns:
        int: The monetary value in milliunits (1/1000th of currency).
    """
    if not amount:
        return 0
    
    try:
        # Remove currency symbols and commas
        clean_amount = ''.join(c for c in amount if c.isdigit() or c in '.-')
        if '.' in clean_amount:
            # Split dollars and cents
            dollars, cents = clean_amount.rsplit('.', 1)
            # Ensure cents is padded with zeros if needed
            cents = cents.ljust(2, '0')[:2]
            # Convert to milliunits (multiply by 10 to convert from cents to milliunits)
            result = int(dollars) * 1000 - int(cents) * 10 if dollars.startswith('-') else int(dollars) * 1000 + int(cents) * 10
            return result
        else:
            # Convert to milliunits
            result = int(clean_amount) * 1000
            return result
    except ValueError:
        return 0

def create_import_id(data: str) -> str:
    """Create import ID with hash of data"""
    hash_obj = hashlib.sha256(data.encode())
    return f"{VERSIONED_IMPORT_PREFIX}{hash_obj.hexdigest()}"[:36]

class Trading212API:
    """Simple client for Trading 212 API"""
    # Updated to support both live and demo environments
    BASE_URL_LIVE = "https://live.trading212.com/api/v0"
    BASE_URL_DEMO = "https://demo.trading212.com/api/v0"
    
    # Rate limit information from Trading 212 API docs
    # Format: endpoint: (requests_limit, time_period_seconds)
    RATE_LIMITS = {
        "GET /history/exports": (1, 60),    # 1 request per minute
        "POST /history/exports": (1, 30),   # 1 request per 30 seconds
    }
    
    def __init__(self, api_token: str, use_demo: bool = False):
        self.api_token = api_token
        self.base_url = self.BASE_URL_DEMO if use_demo else self.BASE_URL_LIVE
        self.headers = {
            "Authorization": api_token,
            "Accept": "application/json"
        }
        # Track last request time for rate limiting
        self.last_request_times = {}
    
    def _make_request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        """
        Make an API request with rate limiting and retry logic
        """
        url = f"{self.base_url}{endpoint}"
        
        # Check if we need to wait for rate limits
        rate_limit_key = f"{method} {endpoint.split('?')[0]}"
        if rate_limit_key in self.RATE_LIMITS:
            _, period_seconds = self.RATE_LIMITS[rate_limit_key]
            
            if rate_limit_key in self.last_request_times:
                elapsed = time.time() - self.last_request_times[rate_limit_key]
                if elapsed < period_seconds:
                    # Wait for rate limit to reset with a small buffer
                    wait_time = period_seconds - elapsed + 1
                    print(f"Rate limit: Waiting {wait_time:.1f} seconds before next request...")
                    time.sleep(wait_time)
        
        # Exponential backoff settings
        max_retries = 5
        base_delay = 2
        
        for retry in range(max_retries):
            try:
                response = requests.request(method, url, headers=self.headers, **kwargs)
                
                # Record the time of this request
                self.last_request_times[rate_limit_key] = time.time()
                
                # Handle rate limiting
                if response.status_code == 429:
                    retry_after = int(response.headers.get('Retry-After', base_delay * (2 ** retry)))
                    print(f"Rate limited. Waiting {retry_after} seconds before retry...")
                    time.sleep(retry_after)
                    continue
                
                # Raise exception for other errors
                response.raise_for_status()
                return response
                
            except requests.exceptions.RequestException as e:
                if retry == max_retries - 1:
                    raise
                
                # Calculate backoff time with jitter
                delay = base_delay * (2 ** retry) + random.uniform(0, 1)
                print(f"Request failed: {e}. Retrying in {delay:.1f} seconds...")
                time.sleep(delay)
        
        # This should not be reached if max_retries > 0
        raise RuntimeError("Maximum retries exceeded")
    
    def request_export(self, from_date: str, to_date: str) -> Dict[str, Any]:
        """Request a new export of transaction data"""
        endpoint = "/history/exports"
        payload = {
            "dataIncluded": {
                "includeDividends": True,
                "includeInterest": True, 
                "includeOrders": True,
                "includeTransactions": True
            },
            "timeFrom": from_date,
            "timeTo": to_date
        }
        
        response = self._make_request("POST", endpoint, json=payload)
        return response.json()
    
    def get_exports(self) -> List[Dict[str, Any]]:
        """Get the list of exports"""
        endpoint = "/history/exports"
        response = self._make_request("GET", endpoint)
        return response.json()
    
    def download_csv(self, download_link: str) -> str:
        """Download the CSV content from a link"""
        # Direct download doesn't go through the API, so no need for rate limiting
        response = requests.get(download_link)
        response.raise_for_status()
        return response.text

def get_trading212_transactions(
    csv_path: Optional[str] = None, 
    api_token: Optional[str] = None,
    use_demo: bool = False,
    days: Optional[int] = None,
    start_date: Optional[str] = None,
    save_raw_csv: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Get Trading 212 transactions either from a local CSV file or by fetching from the API
    
    Args:
        csv_path: Path to a local CSV file
        api_token: Trading 212 API token
        use_demo: Whether to use the demo environment
        days: Number of days in the past to fetch transactions for
        start_date: Start date in DD/MM/YYYY format
        save_raw_csv: Path to save the raw CSV content before processing
    """
    csv_content = None
    
    # If CSV path is provided, read from the file
    if csv_path:
        with open(csv_path, 'r', encoding='utf-8') as f:
            csv_content = f.read()
    
    # Otherwise, fetch from Trading 212 API
    elif api_token:
        api = Trading212API(api_token, use_demo)
        
        # Set up date range based on parameters
        today = datetime.datetime.now(datetime.UTC)
        
        if start_date:
            # Parse start date from DD/MM/YYYY format
            try:
                parsed_date = datetime.datetime.strptime(start_date, "%d/%m/%Y")
                # Set to beginning of day and make timezone-aware
                from_date = datetime.datetime(
                    parsed_date.year, 
                    parsed_date.month, 
                    parsed_date.day, 
                    0, 0, 0, 
                    tzinfo=datetime.UTC
                )
            except ValueError:
                raise ValueError("Invalid date format. Please use DD/MM/YYYY")
        elif days:
            # Calculate date from days ago, set to beginning of day
            from_date = today - datetime.timedelta(days=days)
            from_date = datetime.datetime(
                from_date.year,
                from_date.month,
                from_date.day,
                0, 0, 0,
                tzinfo=datetime.UTC
            )
        else:
            # Default: one year ago
            one_year_ago = today - datetime.timedelta(days=365)
            from_date = datetime.datetime(
                one_year_ago.year,
                one_year_ago.month,
                one_year_ago.day,
                0, 0, 0,
                tzinfo=datetime.UTC
            )
        
        print(f"Fetching transactions from {from_date.strftime('%d/%m/%Y')} to {today.strftime('%d/%m/%Y')}")
        
        print("Requesting new export from Trading 212...")
        export_request = api.request_export(
            from_date.isoformat(), 
            today.isoformat()
        )
        report_id = export_request.get("reportId")
        
        if not report_id:
            raise ValueError("Failed to get reportId from export request")
        
        # Poll for export completion
        max_attempts = 30
        for attempt in range(max_attempts):
            print(f"Checking export status (attempt {attempt+1}/{max_attempts})...")
            
            exports = api.get_exports()
            export = next((e for e in exports if e.get("reportId") == report_id), None)
            
            if not export:
                raise ValueError(f"Could not find export with reportId {report_id}")
            
            status = export.get("status")
            print(f"Export status: {status}")
            
            if status == "Finished":
                download_link = export.get("downloadLink")
                if download_link:
                    print("Export ready. Downloading...")
                    csv_content = api.download_csv(download_link)
                    break
            elif status == "Failed":
                raise ValueError("Export failed on Trading 212 server")
            
            # Wait before trying again - use a longer wait time to avoid rate limits
            wait_time = 15 + random.uniform(0, 5)
            print(f"Waiting {wait_time:.1f} seconds before checking again...")
            time.sleep(wait_time)
        
        if not csv_content:
            raise TimeoutError("Export did not complete within the expected time")
    
    else:
        raise ValueError("Either csv_path or api_token must be provided")
    
    # Save raw CSV content if requested
    if save_raw_csv and csv_content:
        with open(save_raw_csv, 'w', encoding='utf-8') as f:
            f.write(csv_content)
        print(f"Saved raw CSV content to {save_raw_csv}")
    
    # Parse the CSV content
    reader = csv.DictReader(StringIO(csv_content))
    transactions = []
    
    for row in reader:
        # Convert CSV row to our transaction format
        transaction = {
            "action": row.get("Action", ""),
            "timestamp": row.get("Time", ""),
            "isin": row.get("ISIN", ""),
            "ticker": row.get("Ticker", ""),
            "name": row.get("Name", ""),
            "shareCount": row.get("No. of shares", ""),
            "pricePerShare": row.get("Price / share", ""),
            "pricePerShareCurrency": row.get("Currency (Price / share)", ""),
            "exchangeRate": row.get("Exchange rate", ""),
            "result": row.get("Result", ""),
            "resultCurrency": row.get("Currency (Result)", ""),
            "total": parse_money(row.get("Total", "0")),
            "totalCurrency": row.get("Currency (Total)", ""),
            "withholdingTax": row.get("Withholding tax", ""),
            "withholdingTaxCurrency": row.get("Currency (Withholding tax)", ""),
            "notes": row.get("Notes", ""),
            "id": row.get("ID", ""),
            # Currency conversion details
            "conversionFromAmount": row.get("Currency conversion from amount", ""),
            "conversionFromCurrency": row.get("Currency (Currency conversion from amount)", ""),
            "conversionToAmount": row.get("Currency conversion to amount", ""),
            "conversionToCurrency": row.get("Currency (Currency conversion to amount)", ""),
            "conversionFee": row.get("Currency conversion fee", ""),
            "conversionFeeCurrency": row.get("Currency (Currency conversion fee)", ""),
            # Merchant details for card transactions
            "merchantName": row.get("Merchant name", ""),
            "merchantCategory": row.get("Merchant category", "")
        }
        transactions.append(transaction)
    
    return transactions

def filter_transactions(transactions: List[Dict[str, Any]], selected_types: List[str]) -> List[Dict[str, Any]]:
    """Filter transactions by selected transaction types"""
    return [t for t in transactions if t["action"] in selected_types]

def format_category_name(category: str) -> str:
    """
    Format a category string from UPPERCASE_WITH_UNDERSCORES to Title Case.
    
    Args:
        category (str): The category name to format.
        
    Returns:
        str: The formatted category name.
    """
    if not category:
        return ""
    
    # Replace underscores with spaces and convert to title case
    return " ".join(word.capitalize() for word in category.replace("_", " ").lower().split())

def prepare_ynab_transactions(transactions: List[Dict[str, Any]], account_id: str) -> List[Dict[str, Any]]:
    """Convert Trading 212 transactions to YNAB format"""
    ynab_transactions = []
    
    for t in transactions:
        # Handle timestamps with or without milliseconds
        timestamp = t["timestamp"]
        try:
            # First try parsing with milliseconds
            date = datetime.datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S.%f").strftime("%Y-%m-%d")
        except ValueError:
            try:
                # Then try without milliseconds
                date = datetime.datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d")
            except ValueError:
                # If both fail, just use the date part
                print(f"Warning: Could not parse timestamp '{timestamp}', extracting date part only")
                date = timestamp.split()[0]
        
        import_id = create_import_id(f"{t['timestamp']}:{t['id']}")
        
        # Basic transaction template
        ynab_transaction = {
            "account_id": account_id,
            "date": date,
            "cleared": "cleared",
            "amount": t["total"],
            "import_id": import_id
        }
        
        # Customize based on transaction type
        if t["action"] == Trading212Action.DEPOSIT or t["action"] == Trading212Action.WITHDRAWAL:
            ynab_transaction["payee_name"] = t["action"]
            ynab_transaction["memo"] = t["notes"] if t["notes"] else None
            
        elif t["action"] == Trading212Action.INTEREST_ON_CASH or t["action"] == Trading212Action.LENDING_INTEREST:
            ynab_transaction["payee_name"] = "Interest"
            ynab_transaction["memo"] = "Lending interest" if t["action"] == Trading212Action.LENDING_INTEREST else None
            ynab_transaction["flag_color"] = "purple"
            ynab_transaction["approved"] = t["action"] == Trading212Action.INTEREST_ON_CASH

        elif t["action"] == Trading212Action.CASHBACK:
            ynab_transaction["payee_name"] = "Cashback"
            ynab_transaction["memo"] = t["notes"] if t["notes"] else "Trading 212 Cashback"
            ynab_transaction["flag_color"] = "green"
            ynab_transaction["approved"] = True
            
        elif t["action"] == Trading212Action.DIVIDEND:
            ynab_transaction["payee_name"] = f"Stock: {t['name']}"
            ynab_transaction["memo"] = f"Dividend - {t['shareCount']}x {t['ticker']} [{t['isin']}]"
            
        elif t["action"] == Trading212Action.CARD_DEBIT:
            # Format merchant name for cleaner display
            raw_merchant_name = t["merchantName"] or "Unknown Merchant"
            merchant_name = " ".join(word.capitalize() for word in raw_merchant_name.split())
            ynab_transaction["payee_name"] = merchant_name
            
            # Format category for cleaner display and add to memo
            merchant_category = format_category_name(t["merchantCategory"])
            
            # Add category hint in memo
            memo = ""
            if merchant_category:
                memo += f"Category: {merchant_category}"
            if t["notes"]:
                memo += f" | {t['notes']}"
            
            ynab_transaction["memo"] = memo.strip() if memo else None
            
        elif t["action"] == Trading212Action.CARD_CREDIT:
            # For refunds and credits
            raw_merchant_name = t["merchantName"] or "Unknown Merchant"
            merchant_name = " ".join(word.capitalize() for word in raw_merchant_name.split())
            ynab_transaction["payee_name"] = merchant_name
            
            # Format transaction type and category
            merchant_type = t["notes"] or "Refund"  # Often "REFUND" or "PAYOUT"
            merchant_category = format_category_name(t["merchantCategory"])
            
            memo_parts = []
            if merchant_type:
                memo_parts.append(merchant_type.capitalize())
            if merchant_category:
                memo_parts.append(merchant_category)
                
            ynab_transaction["memo"] = " | ".join(memo_parts) if memo_parts else None
            
        elif t["action"] == Trading212Action.SPENDING_CASHBACK:
            ynab_transaction["payee_name"] = "Cashback Rewards"
            ynab_transaction["memo"] = "Spending cashback"
            ynab_transaction["flag_color"] = "green"
            ynab_transaction["approved"] = True
            
        elif t["action"] == Trading212Action.MARKET_BUY or t["action"] == Trading212Action.MARKET_SELL:
            action_type = "Purchase" if t["action"] == Trading212Action.MARKET_BUY else "Sale"
            ynab_transaction["payee_name"] = f"Stock: {t['name'] or t['ticker']}"
            
            memo_parts = []
            if t["ticker"]:
                memo_parts.append(t["ticker"])
            if t["shareCount"]:
                memo_parts.append(f"{t['shareCount']} shares")
            if t["pricePerShare"] and t["pricePerShareCurrency"]:
                memo_parts.append(f"{t['pricePerShare']} {t['pricePerShareCurrency']}/share")
            
            memo = f"{action_type}: " + ", ".join(memo_parts)
            ynab_transaction["memo"] = memo
        
        ynab_transactions.append(ynab_transaction)
    
    return ynab_transactions

def send_to_ynab(transactions: List[Dict[str, Any]], budget_id: str, ynab_token: str) -> bool:
    """Send transactions to YNAB, relying on YNAB's deduplication"""
    if not transactions:
        print("No transactions to send")
        return True
    
    print(f"Sending {len(transactions)} transactions to YNAB")
    
    headers = {
        "Authorization": f"Bearer {ynab_token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    # Properly structure the payload according to YNAB API docs
    payload = {
        "transactions": transactions
    }
    
    # Updated URL to use api.ynab.com instead of api.youneedabudget.com
    url = f"https://api.ynab.com/v1/budgets/{budget_id}/transactions"
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        
        # Print detailed error information for debugging
        if response.status_code >= 400:
            print(f"Error details: {response.text}")
        
        response.raise_for_status()
        
        # Parse response to check for duplicate transactions
        result = response.json()
        duplicates = result.get("data", {}).get("duplicate_import_ids", [])
        if duplicates:
            print(f"Note: {len(duplicates)} transactions were duplicates (already in YNAB)")
            
        print(f"Successfully sent {len(transactions) - len(duplicates)} new transactions to YNAB")
        return True
    except requests.exceptions.RequestException as e:
        print(f"Error sending transactions to YNAB: {e}")
        if hasattr(e, 'response') and e.response is not None:
            try:
                error_details = e.response.json()
                print(f"Error details: {error_details}")
            except:
                print(f"Raw response: {e.response.text}")
        return False

def save_transactions_to_json(transactions: List[Dict[str, Any]], output_file: str) -> None:
    """Save transactions to a JSON file"""
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(transactions, f, indent=2)
    print(f"Saved {len(transactions)} transactions to {output_file}")

def main():
    parser = argparse.ArgumentParser(description="Process Trading 212 transactions and send to YNAB")
    parser.add_argument("--csv", help="Path to Trading 212 CSV export file")
    parser.add_argument("--output", help="Output JSON file for processed transactions")
    parser.add_argument("--filter", nargs="+", choices=[
        Trading212Action.DEPOSIT, 
        Trading212Action.WITHDRAWAL,
        Trading212Action.MARKET_BUY,
        Trading212Action.MARKET_SELL,
        Trading212Action.DIVIDEND,
        Trading212Action.INTEREST_ON_CASH,
        Trading212Action.LENDING_INTEREST,
        Trading212Action.CURRENCY_CONVERSION,
        Trading212Action.NEW_CARD_COST,
        Trading212Action.CASHBACK,
        Trading212Action.CARD_DEBIT,
        Trading212Action.CARD_CREDIT,
        Trading212Action.SPENDING_CASHBACK
    ], help="Filter by transaction types")
    parser.add_argument("--send", action="store_true", help="Send transactions to YNAB")
    parser.add_argument("--fetch", action="store_true", help="Fetch transactions from Trading 212 API instead of using local CSV")
    parser.add_argument("--demo", action="store_true", help="Use Trading 212 demo environment instead of live")
    
    # Add date range options
    date_group = parser.add_mutually_exclusive_group()
    date_group.add_argument("--days", type=int, help="Number of days in the past to fetch transactions for")
    date_group.add_argument("--start-date", help="Start date in DD/MM/YYYY format")
    
    # Add option to save raw CSV
    parser.add_argument("--save-raw-csv", help="Save the raw CSV content to this file before processing")
    
    # Add option to override import ID version
    parser.add_argument("--id-version", type=int, help="Override the import ID version (use to force new IDs)")
    
    args = parser.parse_args()
    
    # Allow override of import ID version
    if args.id_version:
        global IMPORT_ID_VERSION, VERSIONED_IMPORT_PREFIX
        IMPORT_ID_VERSION = args.id_version
        VERSIONED_IMPORT_PREFIX = f"{IMPORT_PREFIX}v{IMPORT_ID_VERSION}:"
        print(f"Using custom import ID version: {IMPORT_ID_VERSION}")
    
    # Make sure we have either CSV file or API fetch
    if not args.csv and not args.fetch:
        print("Error: Either --csv or --fetch must be specified")
        return
    
    # Set up API token if fetching
    trading212_token = None
    if args.fetch:
        trading212_token = os.environ.get("TRADING212_TOKEN")
        if not trading212_token:
            print("Error: TRADING212_TOKEN environment variable must be set when using --fetch")
            return
    
    # Load transactions
    try:
        transactions = get_trading212_transactions(
            csv_path=args.csv if args.csv else None,
            api_token=trading212_token,
            use_demo=args.demo,
            days=args.days,
            start_date=args.start_date,
            save_raw_csv=args.save_raw_csv
        )
        print(f"Loaded {len(transactions)} transactions")
    except Exception as e:
        print(f"Error loading transactions: {e}")
        return
    
    # Filter transactions if specified
    if args.filter:
        transactions = filter_transactions(transactions, args.filter)
        print(f"Filtered to {len(transactions)} transactions")
    
    # Save to JSON if output file specified
    if args.output:
        save_transactions_to_json(transactions, args.output)
    
    # Send to YNAB if requested
    if args.send:
        ynab_token = os.environ.get("YNAB_TOKEN")
        budget_id = os.environ.get("BUDGET")
        account_id = os.environ.get("ACCOUNT")
        
        if not all([ynab_token, budget_id, account_id]):
            print("Error: YNAB_TOKEN, BUDGET, and ACCOUNT environment variables must be set")
            return
        
        ynab_transactions = prepare_ynab_transactions(transactions, account_id)
        send_to_ynab(ynab_transactions, budget_id, ynab_token)

if __name__ == "__main__":
    main()
