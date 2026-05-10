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
# [설정] DB 경로 및 휴장일
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
        # 오늘 포함 최근 7일, 30일 데이터 조회
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
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="1y")
        if len(hist) < 60: return 0.0
        
        past_data = hist[:-1]
        mean_vol_1y = past_data['Volume'].mean()
        std_vol_1y = past_data['Volume'].std()
        
        z_score = 0.0
        if std_vol_1y > 0:
            z_score = (today_vol - mean_vol_1y) / std_vol_1y
        return float(round(z_score, 2))
    except:
        return 0.0

def get_whale_tracker_data():
    log("🐋 [Whale Tracker] 데이터 수집 및 Z-score 분류 시작...")
    
    report_date = get_target_report_date()
    log(f"   📅 분석 기준일: {report_date}")

    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://finviz.com/screener.ashx'
    }
    
    whale_alerts = []   # Z-score >= 2.0
    normal_alerts = []  # Z-score < 2.0
    seen_tickers = set()

    TARGETS = [
        ("S&P 500", "idx_sp500"),       
        ("Nasdaq All", "exch_nasd"),    
        ("NYSE All", "exch_nyse")       
    ]

    for target_name, filter_code in TARGETS:
        log(f"\n   🔍 [{target_name}] 그룹 스캔 중...")
        max_scan = 61 if "All" in target_name else 41
        
        for start_row in range(1, max_scan, 20):
            url = f"https://finviz.com/screener.ashx?v=111&f={filter_code},sh_relvol_o1.5&ft=4&o=-volume&r={start_row}"
            try:
                res = requests.get(url, headers=headers, timeout=10)
                all_tables = pd.read_html(io.StringIO(res.text), header=0)
                candidate_tables = [t for t in all_tables if 'Ticker' in t.columns]
                if not candidate_tables: continue
                df = max(candidate_tables, key=len)

                for _, row in df.iterrows():
                    try:
                        ticker = str(row['Ticker'])
                        if ticker in seen_tickers: continue
                        seen_tickers.add(ticker)

                        industry = str(row.get('Industry', 'Unknown'))
                        price = float(str(row.get('Price', 0)))
                        vol_str = str(row.get('Volume', '0'))
                        if 'M' in vol_str: volume = int(float(vol_str.replace('M','')) * 1_000_000)
                        elif 'B' in vol_str: volume = int(float(vol_str.replace('B','')) * 1_000_000_000)
                        elif 'K' in vol_str: volume = int(float(vol_str.replace('K','')) * 1_000)
                        else: volume = int(vol_str)

                        # Z-score 계산
                        z_score = calculate_metrics(ticker, volume)
                        
                        weekly_freq, monthly_freq = 0, 0
                        if z_score >= 2.0:
                            data = {'ticker': ticker, 'date': report_date, 'price': price,
                                    'volume': volume, 'z_score': z_score}
                            save_whale_event(data)
                            weekly_freq, monthly_freq = get_frequency(ticker)
                            log(f"      🚨 [고래포착] {ticker} (Z:{z_score})")

                        item_data = {
                            "ticker": ticker,
                            "industry": industry.replace("Exchange Traded Fund", "ETF"),
                            "z_score": z_score,
                            "weekly_freq": weekly_freq,
                            "monthly_freq": monthly_freq
                        }

                        if z_score >= 2.0:
                            whale_alerts.append(item_data)
                        else:
                            normal_alerts.append(item_data)

                    except: continue
                time.sleep(random.uniform(1, 2))
            except Exception as e:
                log(f"   ⚠️ 에러 ({target_name}): {e}")
                break
    
    # Z-score 기준 내림차순 정렬
    whale_alerts.sort(key=lambda x: x['z_score'], reverse=True)
    normal_alerts.sort(key=lambda x: x['z_score'], reverse=True)
    
    log(f"\n✅ 분석 완료. 고래: {len(whale_alerts)}개, 활동성: {len(normal_alerts)}개 리포팅.")
    
    return {
        "whale_alerts": whale_alerts,
        "normal_alerts": normal_alerts
    }

if __name__ == "__main__":
    get_whale_tracker_data()