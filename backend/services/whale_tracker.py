import sqlite3
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
import time
import random 
import os
import io

# ---------------------------------------------------------
# [Memory] 패키지 추가 시: pip freeze > requirements.txt
# ---------------------------------------------------------

DB_PATH = "whale_tracker.db"

# 휴장일 리스트 (주요 미국 공휴일)
NYSE_HOLIDAYS = [
    "2025-01-01", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26", 
    "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
]

# =========================================================
# 📅 유틸리티
# =========================================================
def get_target_report_date():
    """주말 및 휴장일을 건너뛰고 가장 최근 평일(거래일) 반환"""
    target_date = datetime.now() - timedelta(days=1)
    while True:
        date_str = target_date.strftime('%Y-%m-%d')
        weekday = target_date.weekday()
        if weekday >= 5 or date_str in NYSE_HOLIDAYS:
            target_date -= timedelta(days=1)
            continue
        return date_str

def get_frequency(ticker):
    """DB에서 최근 7일/30일 고래 출몰 빈도 조회"""
    if not os.path.exists(DB_PATH): return 0, 0
    
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

def save_whale_event(data):
    """Z-score 2.0 이상인 '진짜 고래'만 DB에 영구 저장"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT OR IGNORE INTO daily_whale 
            (ticker, date, price, volume, z_score, rel_volume, is_whale_day)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            data['ticker'], data['date'], data['price'], 
            data['volume'], data['z_score'], data['rel_volume'], 1
        ))
        conn.commit()
    except Exception as e:
        print(f"   ⚠️ DB 저장 에러: {e}")
    finally:
        conn.close()

def calculate_z_score(ticker, today_vol):
    """
    yfinance를 통해 실시간으로 과거 1년치 데이터를 받아와 Z-score 계산
    (로컬 DB가 아닌 외부 실제 데이터를 쓰므로 모든 종목 계산 가능)
    """
    try:
        stock = yf.Ticker(ticker)
        # 1년치 데이터 요청
        hist = stock.history(period="1y")
        if len(hist) < 20: return 0.0
        
        # 오늘 데이터를 제외한 과거 통계 산출
        past_data = hist[:-1]
        mean_vol = past_data['Volume'].mean()
        std_vol = past_data['Volume'].std()
        
        if std_vol == 0: return 0.0
        
        return round((today_vol - mean_vol) / std_vol, 2)
    except:
        return 0.0

# =========================================================
# 🚀 메인 로직
# =========================================================
def get_whale_tracker_data():
    print("🐋 [Whale Tracker] 1차 필터(Finviz) + 2차 검증(Z-score) 시작...")
    
    if not os.path.exists(DB_PATH):
        print("   ❌ DB 파일이 없습니다. 'init_whale_db.py'를 먼저 실행해주세요.")
        return []

    report_date = get_target_report_date()
    print(f"   📅 분석 기준일: {report_date}")

    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://finviz.com/screener.ashx'
    }
    
    results = []
    seen_tickers = set()

    TARGETS = [
        ("S&P 500", "idx_sp500"),       
        ("Nasdaq All", "exch_nasd"),    
        ("NYSE All", "exch_nyse")       
    ]

    for target_name, filter_code in TARGETS:
        print(f"\n   🔍 [{target_name}] 그룹 스캔 중...")
        
        max_scan = 61 if "All" in target_name else 41
        
        for start_row in range(1, max_scan, 20):
            url = f"https://finviz.com/screener.ashx?v=111&f={filter_code},sh_relvol_o1.5&ft=4&o=-volume&r={start_row}"
            
            try:
                res = requests.get(url, headers=headers, timeout=10)
                
                # [효율적 파싱] 모든 테이블 읽기 -> 'Ticker' 있는 것 찾기
                try:
                    all_tables = pd.read_html(io.StringIO(res.text), header=0)
                except ValueError: break # 테이블 없음

                df = None
                for table in all_tables:
                    if 'Ticker' in table.columns:
                        df = table
                        break
                
                if df is None: continue # 데이터 테이블 없음

                for index, row in df.iterrows():
                    try:
                        ticker = str(row['Ticker'])
                        if ticker in seen_tickers: continue
                        seen_tickers.add(ticker)

                        price = float(str(row.get('Price', 0)))
                        rel_vol = float(str(row.get('Rel Volume', 0)))
                        
                        vol_str = str(row.get('Volume', '0'))
                        if 'M' in vol_str: volume = int(float(vol_str.replace('M','')) * 1_000_000)
                        elif 'B' in vol_str: volume = int(float(vol_str.replace('B','')) * 1_000_000_000)
                        elif 'K' in vol_str: volume = int(float(vol_str.replace('K','')) * 1_000)
                        else: volume = int(vol_str)

                    except: continue

                    # ----------------------------------------------------
                    # [핵심 로직] 2차 검증 및 데이터 분류
                    # ----------------------------------------------------
                    z_score = calculate_z_score(ticker, volume)
                    
                    is_real_whale = (z_score >= 2.0)
                    
                    weekly_freq, monthly_freq = 0, 0
                    
                    if is_real_whale:
                        # 1. 진짜 고래(Z >= 2.0) -> DB 저장 & 빈도 조회
                        data = {'ticker': ticker, 'date': report_date, 'price': price,
                                'volume': volume, 'z_score': z_score, 'rel_volume': rel_vol}
                        save_whale_event(data)
                        
                        weekly_freq, monthly_freq = get_frequency(ticker)
                        status_msg = f"🔥 발견! (Z-score {z_score} / 월간 {monthly_freq}회)"
                        print(f"      🚨 [고래] {ticker} - {status_msg}")
                    else:
                        # 2. 일반 급등(Z < 2.0) -> DB 저장 안 함, 단순 리포팅
                        status_msg = f"⚪ 거래량 증가 (Z-score {z_score})"
                        # print(f"      [일반] {ticker} - {status_msg}") # 로그 너무 많으면 주석 처리

                    # 결과 리스트에는 '모두' 담습니다.
                    results.append({
                        "ticker": ticker,
                        "group": target_name,
                        "date": report_date,
                        "price": f"${price}",
                        "volume": f"{volume:,}",
                        "rel_volume": rel_vol,
                        "z_score": z_score,
                        "is_whale": is_real_whale,   # 프론트엔드에서 강조용 (True/False)
                        "weekly_freq": weekly_freq,  # 고래가 아니면 0
                        "monthly_freq": monthly_freq, # 고래가 아니면 0
                        "msg": status_msg
                    })
                
                time.sleep(random.uniform(2, 4)) # 봇 탐지 회피

            except Exception as e:
                # print(f"   ⚠️ 에러 ({target_name}): {e}")
                break
    
    # 중요: 결과 리스트를 Z-score 높은 순으로 정렬해서 리턴
    results.sort(key=lambda x: x['z_score'], reverse=True)
    
    print(f"\n✅ 분석 완료. 총 {len(results)}개 종목 리포팅.")
    return results