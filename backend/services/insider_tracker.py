import cloudscraper
import pandas as pd
import io
import yfinance as yf
from datetime import datetime, timedelta
import time
import random
from concurrent.futures import ThreadPoolExecutor, as_completed # For parallel processing
from collections import defaultdict

# ---------------------------------------------------------
# [Configuration] Filtering Criteria (USD)
# ---------------------------------------------------------
LOOKBACK_DAYS = 30              

# Internal precision filter criteria
MIN_PRICE = 3.0                 # Stock price >= $3
MIN_MARKET_CAP = 250_000_000    # Market Cap >= $250M
MIN_DAILY_TURNOVER = 2_500_000  # Daily Turnover >= $2.5M

# [Trade Value Criteria]
MIN_TRADE_VALUE_CLUSTER = 20000 # [Cluster] >= $20k with 3+ insiders considered a cluster buy
MIN_TRADE_VALUE_REPORT = 100000 # [Significant Buy] >= $100k
MIN_SELL_VALUE_FILTER = 400000  # [Sell] Monitor sells >= $400k

# [Parallel Processing Setup]
MAX_WORKERS = 10 # Max concurrent workers (too high may cause blocks)

C_LEVEL_TITLES = ["CEO", "Chief Executive Officer", "CFO", "Chief Financial Officer", "President"]

def log(msg):
    print(msg, flush=True)

def is_c_level(title):
    title_lower = str(title).lower()
    for c_title in C_LEVEL_TITLES:
        if c_title.lower() in title_lower: return True
    return False

# [Financial Validation - Single Ticker]
def check_financial_health_single(ticker):
    try:
        stock = yf.Ticker(ticker)
        
        # Network call (Most time-consuming part)
        try:
            info = stock.fast_info
            market_cap = info.market_cap
        except:
            return ticker, False, "Data unavailable", "N/A"

        if market_cap is None or market_cap < MIN_MARKET_CAP:
            return ticker, False, "Market cap too low", "N/A"

        try:
            last_vol = info.last_volume
            last_price = info.last_price
            if last_vol is None: last_vol = 0
            if last_price is None: last_price = 0
            
            turnover = last_vol * last_price
            if turnover < MIN_DAILY_TURNOVER:
                return ticker, False, "Turnover too low", "N/A"
        except:
            return ticker, False, "No trade info", "N/A"

        # [Updated] Add industry information for passed tickers
        try:
            industry = stock.info.get('industry', 'N/A')
        except:
            industry = 'N/A'

        return ticker, True, "Pass", industry
    except Exception:
        return ticker, False, "Fetch error", "N/A"

def get_insider_trades():
    cutoff_date = datetime.now() - timedelta(days=LOOKBACK_DAYS)
    log(f"🕵️‍♂️ [Insider Tracker] Starting in-depth analysis (Parallel mode)...")
    
    scraper = cloudscraper.create_scraper()
    base_url = "http://openinsider.com/screener"
    
    # Request Raw Data
    params = {'cnt': 5000, 'xp': 1, 'xs': 1, 'isc': 1}

    try:
        log("   📡 Requesting OpenInsider data...")
        res = scraper.get(base_url, params=params)
        
        if res.status_code != 200:
            log(f"   ⚠️ Connection failed: {res.status_code}")
            return empty_result()

        try:
            dfs = pd.read_html(io.StringIO(res.text), header=0)
        except ValueError: return empty_result()

        if not dfs: return empty_result()
        
        # Find the correct table
        candidate_tables = []
        for table in dfs:
            clean_cols = [str(c).replace('\n', '').replace('\xa0', ' ').strip() for c in table.columns]
            table.columns = clean_cols
            if 'Ticker' in clean_cols and 'Trade Type' in clean_cols:
                candidate_tables.append(table)
        
        if not candidate_tables: return empty_result()
        df = max(candidate_tables, key=len)
        log(f"   ✅ Raw data secured (Total {len(df)} rows).")

        # Date Filtering
        date_col = next((c for c in df.columns if 'Filing' in c and 'Date' in c), 'Filing Date')
        df['dt_parsed'] = pd.to_datetime(df[date_col], errors='coerce')
        df = df.sort_values(by='dt_parsed', ascending=False)
        df_filtered = df[df['dt_parsed'] >= cutoff_date].copy()
        
        if df_filtered.empty:
            log("   ⚠️ No recent data found.")
            return empty_result()

        newest = df_filtered['dt_parsed'].max().strftime('%Y-%m-%d')
        oldest = df_filtered['dt_parsed'].min().strftime('%Y-%m-%d')
        log(f"   📅 [Date Range Confirmed] {newest} ~ {oldest} (Total {len(df_filtered)} rows)")

        # -----------------------------------------------------------
        # [Step 1] Initial Filtering & Identify Financial Validation Targets
        # -----------------------------------------------------------
        log("   🔍 Extracting targets for initial filtering and financial validation...")
        
        type_col = next((c for c in df.columns if 'Trade' in c and 'Type' in c), 'Trade Type')
        ticker_col = 'Ticker'
        insider_col = next((c for c in df.columns if 'Insider' in c), 'Insider Name')
        val_col = next((c for c in df.columns if 'Value' in c), 'Value')
        price_col = next((c for c in df.columns if 'Price' in c), 'Price')

        # Calculate Cluster Candidates
        cluster_candidates = {} 
        for _, row in df_filtered.iterrows():
            trade_type = str(row.get(type_col, ''))
            if 'Purchase' in trade_type and 'OE' not in trade_type:
                val_str = str(row.get(val_col, '0')).replace('$','').replace(',','').replace('+','').strip()
                try: val = float(val_str)
                except: val = 0.0
                if val >= MIN_TRADE_VALUE_CLUSTER:
                    ticker = str(row.get(ticker_col, ''))
                    insider = str(row.get(insider_col, ''))
                    if ticker not in cluster_candidates: cluster_candidates[ticker] = set()
                    cluster_candidates[ticker].add(insider)

        # Extract unique tickers requiring financial validation
        tickers_to_check = set()
        
        # Simulate initial filtering to isolate tickers for yfinance API
        for _, row in df_filtered.iterrows():
            trade_type = str(row.get(type_col, ''))
            if 'OE' in trade_type: continue
            
            is_purchase = 'Purchase' in trade_type
            is_sale = 'Sale' in trade_type
            if not (is_purchase or is_sale): continue

            val_str = str(row.get(val_col, '0')).replace('$','').replace(',','').replace('+','').strip()
            try: value = float(val_str)
            except: value = 0.0

            price_str = str(row.get(price_col, '0')).replace('$','').strip()
            try: price = float(price_str)
            except: price = 0.0

            # 1st Filter: Penny stocks / Value below threshold
            if price < MIN_PRICE: continue
            
            # Value Check
            ticker = str(row.get(ticker_col, ''))
            is_cluster = False
            if ticker in cluster_candidates and len(cluster_candidates[ticker]) >= 3:
                is_cluster = True

            pass_value = False
            if is_sale:
                if value >= MIN_SELL_VALUE_FILTER: pass_value = True
            else: # Purchase
                if is_cluster:
                    if value >= MIN_TRADE_VALUE_CLUSTER: pass_value = True
                else:
                    title_col = next((c for c in df.columns if 'Title' in c), 'Title')
                    is_c_lvl = is_c_level(str(row.get(title_col, '')))
                    if value >= MIN_TRADE_VALUE_REPORT or is_c_lvl: pass_value = True
            
            # Requires financial check only if passed 1st filter + is a Purchase
            if pass_value and is_purchase:
                tickers_to_check.add(ticker)

        log(f"   ⚡ Tickers requiring financial validation: {len(tickers_to_check)} (Starting parallel processing)")

        # -----------------------------------------------------------
        # [Step 2] Parallel Processing (Multi-threading) for Batch Financial Data Collection
        # -----------------------------------------------------------
        financial_cache = {} # {ticker: (is_healthy, industry)} [Updated] Caching industry information
        
        if tickers_to_check:
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                # Submit tasks
                future_to_ticker = {executor.submit(check_financial_health_single, t): t for t in tickers_to_check}
                
                # Collect results
                completed_cnt = 0
                for future in as_completed(future_to_ticker):
                    # [Updated] Receive and cache industry info along with health status
                    ticker, is_healthy, reason, industry = future.result()
                    financial_cache[ticker] = (is_healthy, industry)
                    
                    completed_cnt += 1
                    if completed_cnt % 10 == 0:
                        print(f"      ... Collecting financial data ({completed_cnt}/{len(tickers_to_check)}) ...", flush=True)

        log("   ✅ Financial data collection complete. Generating final report...")

        # -----------------------------------------------------------
        # [Step 3] Final Assembly (Main Loop)
        # -----------------------------------------------------------
        cluster_buys = []
        significant_buys = []
        significant_sells = []
        
        stats = {'skip_oe': 0, 'skip_penny': 0, 'skip_amt': 0, 'skip_fin': 0, 'valid': 0}

        for index, row in df_filtered.iterrows():
            try:
                trade_type = str(row.get(type_col, ''))
                if 'OE' in trade_type:
                    stats['skip_oe'] += 1
                    continue
                
                is_purchase = 'Purchase' in trade_type
                is_sale = 'Sale' in trade_type
                if not (is_purchase or is_sale): continue

                val_str = str(row.get(val_col, '0')).replace('$','').replace(',','').replace('+','').strip()
                try: value = float(val_str)
                except: value = 0.0

                price_str = str(row.get(price_col, '0')).replace('$','').strip()
                try: price = float(price_str)
                except: price = 0.0

                if price < MIN_PRICE:
                    stats['skip_penny'] += 1
                    continue

                ticker = str(row.get(ticker_col, ''))
                
                is_cluster = False
                buyer_count = 0
                if ticker in cluster_candidates:
                    buyer_count = len(cluster_candidates[ticker])
                    if buyer_count >= 3: is_cluster = True

                # Value Filter
                pass_value = False
                if is_sale:
                    if value >= MIN_SELL_VALUE_FILTER: pass_value = True
                else:
                    if is_cluster:
                        if value >= MIN_TRADE_VALUE_CLUSTER: pass_value = True
                    else:
                        title_col = next((c for c in df.columns if 'Title' in c), 'Title')
                        titles = str(row.get(title_col, ''))
                        is_c_lvl = is_c_level(titles)
                        if value >= MIN_TRADE_VALUE_REPORT or is_c_lvl: pass_value = True
                
                if not pass_value:
                    stats['skip_amt'] += 1
                    continue

                # Financial Filter (Use Cache)
                industry = "N/A" # [Updated] Changed variable name from sector to industry
                if is_purchase:
                    # Extract health status and industry details from cache
                    cache_data = financial_cache.get(ticker, (False, "N/A"))
                    is_healthy = cache_data[0]
                    industry = cache_data[1]
                    
                    if not is_healthy:
                        stats['skip_fin'] += 1
                        continue

                stats['valid'] += 1

                # Assemble Data
                insider = str(row.get(insider_col, ''))
                title_col = next((c for c in df.columns if 'Title' in c), 'Title')
                titles = str(row.get(title_col, ''))
                date_str = row['dt_parsed'].strftime('%Y-%m-%d')
                is_c_lvl = is_c_level(titles)

                # [Updated] Optimized amount formatting ($1.2M or $150k)
                if value >= 1_000_000:
                    amount_str_formatted = f"${value/1_000_000:.1f}M"
                else:
                    amount_str_formatted = f"${int(value/1000)}k"

                trade_data = {
                    "ticker": ticker,
                    "industry": industry,     # [Updated] HTML mapping key changed to industry
                    "name": insider,          
                    "role": titles,
                    "type": trade_type,
                    "date": date_str,
                    "price": f"${price}",
                    "value": value,
                    "amount_str": amount_str_formatted, 
                    "is_c_level": is_c_lvl,
                    "is_cluster": is_cluster,
                    "cluster_count": buyer_count
                }

                if is_purchase:
                    if is_cluster:
                        cluster_buys.append(trade_data)
                    else:
                        significant_buys.append(trade_data)
                elif is_sale:
                    significant_sells.append(trade_data)

            except Exception: continue

        # 1. Group Cluster Buys by ticker
        cluster_groups = defaultdict(list)
        for trade in cluster_buys:
            cluster_groups[trade['ticker']].append(trade)

        # 2. Calculate total value per group and sort internally
        cluster_summary = []
        for ticker, trades in cluster_groups.items():
            total_val = sum(t['value'] for t in trades) # Total per ticker
            trades.sort(key=lambda x: x['value'], reverse=True) # Individual sort within group
            cluster_summary.append({
                'ticker': ticker,
                'total_val': total_val,
                'trades': trades
            })

        # 3. Sort ticker groups based on total value
        cluster_summary.sort(key=lambda x: x['total_val'], reverse=True)

        # 4. Flatten the sorted groups back into a list (for HTML rendering)
        sorted_cluster_buys = []
        for group in cluster_summary:
            sorted_cluster_buys.extend(group['trades'])

        cluster_buys = sorted_cluster_buys
        
        # Significant buys and massive sells are sorted by individual trade value as before
        significant_buys.sort(key=lambda x: x['value'], reverse=True)
        significant_sells.sort(key=lambda x: x['value'], reverse=True)

        log(f"✅ Analysis complete. 👑Cluster: {len(cluster_buys)}, 💎Buy: {len(significant_buys)}, 📉Sell: {len(significant_sells)}")
        log(f"📊 Detailed Filtering Statistics:")
        log(f"   - 🟡 Options Exercise (OE): {stats['skip_oe']}")
        log(f"   - 🪙 Penny Stocks (<${MIN_PRICE}): {stats['skip_penny']}")
        log(f"   - 💸 Below Amount Threshold: {stats['skip_amt']}")
        log(f"   - 📉 Failed Financial Check: {stats['skip_fin']}")
        log(f"   - ✅ Final Passed: {stats['valid']}")
        
        return {
            "cluster_buys": cluster_buys,
            "significant_buys": significant_buys,
            "significant_sells": significant_sells
        }

    except Exception as e:
        log(f"   ❌ Error occurred: {e}")
        return empty_result()

def empty_result():
    return {"cluster_buys": [], "significant_buys": [], "significant_sells": []}

if __name__ == "__main__":
    get_insider_trades()