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

# ---------------------------------------------------------
# [Memory] 패키지 추가 시: pip freeze > requirements.txt
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
    target_date = datetime.now() - timedelta(days=1)
    while True:
        date_str = target_date.strftime('%Y-%m-%d')
        weekday = target_date.weekday()
        if weekday >= 5 or date_str in NYSE_HOLIDAYS:
            target_date -= timedelta(days=1)
            continue
        return date_str

def get_frequency(ticker):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
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
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # [DB 스키마 변경] rel_volume 컬럼 삭제됨
        # 만약 "no column named rel_volume" 에러가 나면 기존 db 파일을 삭제해주세요.
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
        log(f"   ⚠️ DB 저장 에러: {e}")

def calculate_metrics(ticker, today_vol):
    """
    yfinance를 이용해 Z-score만 계산 (Rel Volume 삭제)
    Returns: z_score
    """
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="1y")
        
        if len(hist) < 60: return 0.0
        
        past_data = hist[:-1]
        
        # Z-score (1년 기준)
        mean_vol_1y = past_data['Volume'].mean()
        std_vol_1y = past_data['Volume'].std()
        
        z_score = 0.0
        if std_vol_1y > 0:
            z_score = (today_vol - mean_vol_1y) / std_vol_1y
            
        return float(round(z_score, 2))
    except:
        return 0.0

def get_whale_tracker_data():
    log("🐋 [Whale Tracker] 1차 필터(Finviz) + 2차 검증(Z-score only) 시작...")
    
    # DB 파일 삭제 안내
    if os.path.exists(DB_PATH):
        # 스키마가 변경되었으므로 체크 (이 부분은 수동으로 파일 삭제를 권장)
        pass

    report_date = get_target_report_date()
    log(f"   📅 분석 기준일: {report_date}")

    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://finviz.com/screener.ashx'
    }
    
    stock_results = []
    etf_results = []
    seen_tickers = set()

    TARGETS = [
        ("S&P 500", "idx_sp500"),       
        ("Nasdaq All", "exch_nasd"),    
        ("NYSE All", "exch_nyse")       
    ]

    for target_name, filter_code in TARGETS:
        log(f"\n   🔍 [{target_name}] 그룹 스캔 중... (거래량 순)")
        
        max_scan = 61 if "All" in target_name else 41
        etf_count_in_group = 0
        
        # o=-volume: 거래량 내림차순 정렬 (Rel Volume 아님)
        for start_row in range(1, max_scan, 20):
            url = f"https://finviz.com/screener.ashx?v=111&f={filter_code},sh_relvol_o1.5&ft=4&o=-volume&r={start_row}"
            
            try:
                res = requests.get(url, headers=headers, timeout=10)
                
                try:
                    all_tables = pd.read_html(io.StringIO(res.text), header=0)
                except ValueError: continue

                candidate_tables = [t for t in all_tables if 'Ticker' in t.columns]
                if not candidate_tables: continue
                df = max(candidate_tables, key=len)

                for index, row in df.iterrows():
                    ticker = ""
                    industry = ""
                    price = 0.0
                    volume = 0
                    
                    try:
                        ticker = str(row['Ticker'])
                        
                        if ticker in seen_tickers: continue
                        
                        industry = str(row.get('Industry', ''))
                        is_etf = "Exchange Traded Fund" in industry

                        if is_etf and etf_count_in_group >= 10:
                            continue

                        seen_tickers.add(ticker)
                        if is_etf: etf_count_in_group += 1

                        price = float(str(row.get('Price', 0)))
                        
                        vol_str = str(row.get('Volume', '0'))
                        if 'M' in vol_str: volume = int(float(vol_str.replace('M','')) * 1_000_000)
                        elif 'B' in vol_str: volume = int(float(vol_str.replace('B','')) * 1_000_000_000)
                        elif 'K' in vol_str: volume = int(float(vol_str.replace('K','')) * 1_000)
                        else: volume = int(vol_str)

                    except: continue

                    # Z-score만 계산
                    z_score = calculate_metrics(ticker, volume)
                    
                    is_real_whale = bool(z_score >= 2.0)
                    weekly_freq, monthly_freq = 0, 0
                    msg_icon = "⚪"
                    
                    if is_real_whale:
                        # rel_volume 필드 제거됨
                        data = {'ticker': ticker, 'date': report_date, 'price': price,
                                'volume': volume, 'z_score': z_score}
                        save_whale_event(data)
                        weekly_freq, monthly_freq = get_frequency(ticker)
                        msg_icon = "🔥"
                        log(f"      🚨 [포착] {ticker} (Z:{z_score}) - {industry}")

                    item_data = {
                        "ticker": str(ticker),
                        "group": str(target_name),
                        "industry": str(industry),
                        "date": str(report_date),
                        "price": f"${price}",
                        "volume": f"{volume:,}",
                        # rel_volume 제거
                        "z_score": float(z_score),
                        "is_whale": bool(is_real_whale),
                        "weekly_freq": int(weekly_freq),
                        "monthly_freq": int(monthly_freq),
                        "msg": f"{msg_icon} {ticker}"
                    }

                    if is_etf:
                        etf_results.append(item_data)
                    else:
                        stock_results.append(item_data)
                
                time.sleep(random.uniform(2, 4))

            except Exception as e:
                log(f"   ⚠️ 에러 ({target_name}): {e}")
                break
    
    stock_results.sort(key=lambda x: x['z_score'], reverse=True)
    etf_results.sort(key=lambda x: x['z_score'], reverse=True)
    
    log(f"\n✅ 분석 완료. 주식: {len(stock_results)}개, ETF: {len(etf_results)}개 리포팅.")
    
    return {
        "stocks": stock_results,
        "etfs": etf_results
    }

if __name__ == "__main__":
    get_whale_tracker_data()