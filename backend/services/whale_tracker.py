# backend/services/whale_tracker.py

import sqlite3
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
import time
import random 
import os
import io
import numpy as np
import cloudscraper
import re
import concurrent.futures
  

# ---------------------------------------------------------
# [Configuration] Database Path and Market Holidays
# ---------------------------------------------------------
DB_PATH = "whale_tracker.db"
if __name__ == "__main__":
    DB_PATH = "../whale_tracker.db" if os.path.exists("../whale_tracker.db") else "whale_tracker.db"

NYSE_HOLIDAYS = [
    "2025-01-01", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26", 
    "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
]

def log(msg):
    print(msg, flush=True)
    
def get_target_report_date():
    """Finds the most recent valid trading day, skipping weekends and holidays."""
    target_date = datetime.now() - timedelta(days=1)
    while True:
        date_str = target_date.strftime('%Y-%m-%d')
        weekday = target_date.weekday()
        if weekday >= 5 or date_str in NYSE_HOLIDAYS:
            target_date -= timedelta(days=1)
            continue
        return date_str

def get_frequency(ticker):
    """Queries the database to count whale events in the last 7 and 30 days."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Query data for the last 7 and 30 days (including today)
        date_7 = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        date_30 = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        
        cursor.execute("SELECT COUNT(*) FROM daily_whale WHERE ticker = ? AND date >= ?", (ticker, date_7))
        w = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM daily_whale WHERE ticker = ? AND date >= ?", (ticker, date_30))
        m = cursor.fetchone()[0]
        conn.close()
        return w, m
    except:
        return 0, 0

def save_whale_event(data):
    """Saves a detected whale event (Z-score >= 2.0) into the SQLite database."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS daily_whale (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT,
                date TEXT,
                price REAL,
                volume INTEGER,
                z_score REAL,
                is_whale_day INTEGER,
                UNIQUE(ticker, date)
            )
        ''')
        cursor.execute('''
            INSERT OR IGNORE INTO daily_whale 
            (ticker, date, price, volume, z_score, is_whale_day)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            data['ticker'], data['date'], data['price'], 
            data['volume'], data['z_score'], 1
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        log(f"   ⚠️ DB Save Error: {e}")

def calculate_metrics(ticker, today_vol):
    """Calculates the volume Z-score using historical data from yfinance."""
    try:
        yf_ticker = ticker.replace('.', '-')
        
        stock = yf.Ticker(yf_ticker)
        hist = stock.history(period="1y")
        
        if len(hist) < 60: 
            return None 
        
        past_data = hist[:-1]
        mean_vol_1y = past_data['Volume'].mean()
        std_vol_1y = past_data['Volume'].std()
        
        # [수정됨] 표준편차가 0보다 클 때만 정상 계산하고, 아니면 데이터 오류로 간주하여 과감히 버림(None)
        if std_vol_1y > 0:
            z_score = (today_vol - mean_vol_1y) / std_vol_1y
            return float(round(z_score, 2))
        else:
            return None # 표준편차가 0이면 이상 종목으로 간주하여 리스트에서 제외
            
    except:
        return None
    
# [Added] Market Cap Categorization Function
def categorize_market_cap(mc_str):
    """Categorizes market capitalization string into predefined size brackets."""
    if pd.isna(mc_str) or mc_str == '-': return "-"
    try:
        val = float(mc_str.replace('B','').replace('M','').replace('K',''))
        if 'B' in mc_str:
            if val >= 200: return "Mega"
            elif val >= 10: return "Large"
            elif val >= 2: return "Mid"
            else: return "Small"
        elif 'M' in mc_str:
            if val >= 300: return "Small"
            elif val >= 50: return "Micro"
            else: return "Nano"
        return "Nano"
    except:
        return "-"

def fetch_and_calc_zscore(item):
    ticker = item['ticker']
    today_vol = item['volume']
    
    # 앞에서 수정한 None 반환 calculate_metrics 사용
    z_score = calculate_metrics(ticker, today_vol)
    
    if z_score is None:
        return None
        
    item['z_score'] = z_score
    return item

def get_whale_tracker_data():
    """Main execution function for collecting data and classifying Z-scores."""
    log("🐋 [Whale Tracker] Starting data collection and Z-score classification...")
    
    report_date = get_target_report_date()
    log(f"   📅 Target Analysis Date: {report_date}")
    
    scraper = cloudscraper.create_scraper()
    
    whale_alerts = []
    normal_alerts = []
    seen_tickers = set()
    candidate_list = []

    TARGETS = [
        ("S&P 500", "idx_sp500"),       
        ("Nasdaq All", "exch_nasd"),    
        ("NYSE All", "exch_nyse")       
    ]

    MAX_RETRIES = 3  

    for target_name, filter_code in TARGETS:
        log(f"\n   🔍 Scanning [{target_name}] group...")
        max_scan = 201  
        
        break_pagination = False 
        
        for start_row in range(1, max_scan, 20):
            if break_pagination:
                break
                
            url = f"https://finviz.com/screener.ashx?v=111&f={filter_code},sh_relvol_o1.5&ft=4&o=-volume&r={start_row}"
            success = False
            
            for attempt in range(MAX_RETRIES):
                try:
                    res = scraper.get(url, timeout=10)
                    
                    if "unusual activity" in res.text.lower() or "captcha" in res.text.lower():
                        log(f"   ⚠️ Finviz 차단 감지! 우회 재시도 중... ({attempt+1}/{MAX_RETRIES})")
                        time.sleep(random.uniform(3, 5))
                        scraper = cloudscraper.create_scraper() 
                        continue
                        
                    all_tables = pd.read_html(io.StringIO(res.text), header=0)
                    candidate_tables = [t for t in all_tables if 'Ticker' in t.columns]
                    
                    if not candidate_tables: 
                        break_pagination = True
                        success = True
                        break
                        
                    df = max(candidate_tables, key=len)
                    if df.empty or len(df) == 0:
                        break_pagination = True
                        success = True
                        break

                    # -------------------------------------------------------------
                    # 💡 [핵심 버그 수정 2탄] 정규식 패턴 다양화 및 무적의 Fallback(안전장치)
                    # -------------------------------------------------------------
                    # Finviz의 HTML 구조가 바뀔 것을 대비해 여러 패턴으로 진짜 티커명을 찾습니다.
                    patterns = [
                        r'quote\.ashx\?t=([A-Za-z0-9]+)', 
                        r'href="[^"]*\?t=([A-Za-z0-9]+)',
                        r'screener-link-primary">([A-Za-z0-9]+)<'
                    ]
                    
                    valid_tickers_list = []
                    for p in patterns:
                        valid_tickers_list.extend(re.findall(p, res.text, re.IGNORECASE))
                        
                    valid_tickers_set = set([t.upper() for t in valid_tickers_list])
                    
                    for _, row in df.iterrows():
                        try:
                            ticker_raw = str(row['Ticker']).strip().upper()
                            ticker = ticker_raw
                            
                            if valid_tickers_set:
                                # [플랜 A] 정규식으로 진짜 티커 목록을 성공적으로 찾은 경우
                                if ticker not in valid_tickers_set:
                                    if len(ticker) > 1 and ticker[1:] in valid_tickers_set:
                                        ticker = ticker[1:]
                                    elif len(ticker) > 2 and ticker[2:] in valid_tickers_set:
                                        ticker = ticker[2:]
                                        
                                # 복구에 실패했거나 아예 이상한 데이터면 과감히 버림
                                if ticker not in valid_tickers_set:
                                    continue
                            else:
                                # [플랜 B] 정규식이 실패했을 때의 최후의 안전장치 (Fallback)
                                # 만약 Finviz의 UI 버그(첫 글자 중복)가 감지되면 어림짐작으로 수정해서 강행돌파!
                                if len(ticker) > 1 and ticker[0] == ticker[1]:
                                    ticker = ticker[1:]
                                    
                            if ticker in seen_tickers: continue
                            seen_tickers.add(ticker)
                            
                            raw_sector = str(row.get('Sector', 'Unknown'))
                            raw_industry = str(row.get('Industry', 'Unknown'))
                            market_cap_str = str(row.get('Market Cap', '-'))

                            if "Exchange Traded Fund" in raw_industry:
                                display_sector = "ETF"
                            else:
                                display_sector = raw_sector
                                
                            company_size = categorize_market_cap(market_cap_str)

                            price = float(str(row.get('Price', 0)))
                            vol_str = str(row.get('Volume', '0'))
                            
                            if 'M' in vol_str: volume = int(float(vol_str.replace('M','')) * 1_000_000)
                            elif 'B' in vol_str: volume = int(float(vol_str.replace('B','')) * 1_000_000_000)
                            elif 'K' in vol_str: volume = int(float(vol_str.replace('K','')) * 1_000)
                            else: volume = int(vol_str)

                            candidate_list.append({
                                'ticker': ticker, 'volume': volume, 'price': price,
                                'sector': display_sector, 'size': company_size
                            })
                        except: continue
                    
                    success = True
                    break 

                except Exception as e:
                    if "No tables found" in str(e) and attempt < MAX_RETRIES - 1:
                         log(f"   ⚠️ 차단 의심(표 없음). 재시도... ({attempt+1}/{MAX_RETRIES})")
                         time.sleep(random.uniform(3, 5))
                         scraper = cloudscraper.create_scraper()
                         continue
                    else:
                        break_pagination = True
                        success = True
                        break
                        
            if not success:
                log(f"   ❌ {MAX_RETRIES}번 연속 실패. 해당 타겟 스캔을 포기하고 다음으로 넘어갑니다.")
                break
                
            time.sleep(random.uniform(1.5, 3.5)) 

    log(f"\n   ⚡ [Stage 1 (Finviz) Complete] Total {len(candidate_list)} tickers initially collected.")
    log(f"   ⏳ Starting parallel yfinance download...")

    valid_results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch_and_calc_zscore, item): item for item in candidate_list}
        
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res is not None:
                valid_results.append(res)
    log(f"   ⚡ [Stage 2 (yfinance) Complete] Total {len(valid_results)} valid tickers secured for statistical analysis.")

    for res in valid_results:
        ticker = res['ticker']
        z_score = res['z_score']
        price = res['price']
        volume = res['volume']
        
        weekly_freq, monthly_freq = 0, 0
        
        if z_score >= 2.0:
            data = {'ticker': ticker, 'date': report_date, 'price': price,
                    'volume': volume, 'z_score': z_score}
            save_whale_event(data) 
            weekly_freq, monthly_freq = get_frequency(ticker)
            log(f"      🚨 [Whale Detected] {ticker} (Z:{z_score})")

        item_data = {
            "ticker": ticker,
            "sector": res['sector'],
            "size": res['size'],
            "z_score": z_score,
            "weekly_freq": weekly_freq,
            "monthly_freq": monthly_freq
        }

        if z_score >= 2.0:
            whale_alerts.append(item_data)
        elif z_score > 0:
            normal_alerts.append(item_data)  

    whale_alerts.sort(key=lambda x: x['z_score'], reverse=True)
    normal_alerts.sort(key=lambda x: x['z_score'], reverse=True)
    
    stocks = [item for item in whale_alerts if item['sector'] != 'ETF']
    etfs = [item for item in whale_alerts if item['sector'] == 'ETF']
    
    log(f"\n✅ Analysis complete. Reporting Whales: {len(whale_alerts)} (Stocks: {len(stocks)}, ETFs: {len(etfs)}), Active alerts: {len(normal_alerts)}.")

    return {
        "whale_alerts": whale_alerts,  
        "stocks": stocks,
        "etfs": etfs,
        "normal_alerts": normal_alerts    
    }

if __name__ == "__main__":
    get_whale_tracker_data()