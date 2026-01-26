import sqlite3
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
import time
import os

# =========================================================
# ⚙️ [설정]
# =========================================================
DB_PATH = "whale_tracker.db"

# 미국 주식시장 휴장일 (2025~2026년 주요 공휴일)
NYSE_HOLIDAYS = [
    "2025-01-01", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26", 
    "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
]

# =========================================================
# 📅 날짜 유틸리티 (휴장일 체크)
# =========================================================
def get_target_report_date():
    """
    분석 대상 날짜(전 거래일)를 계산하는 함수
    주말 및 휴장일을 건너뛰고 가장 최근 평일을 반환
    """
    target_date = datetime.now() - timedelta(days=1)
    
    while True:
        date_str = target_date.strftime('%Y-%m-%d')
        weekday = target_date.weekday() # 0:월 ~ 6:일
        
        # 1. 주말 체크
        if weekday >= 5:
            # print(f"   💤 {date_str}은 주말입니다. 하루 더 뒤로 갑니다.")
            target_date -= timedelta(days=1)
            continue
            
        # 2. 휴장일 체크
        if date_str in NYSE_HOLIDAYS:
            # print(f"   💤 {date_str}은 휴장일입니다. 하루 더 뒤로 갑니다.")
            target_date -= timedelta(days=1)
            continue
            
        return date_str

# =========================================================
# 🗄️ DB 핸들링
# =========================================================
def get_frequency(ticker):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    date_7 = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    date_30 = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    
    cursor.execute("SELECT COUNT(*) FROM daily_whale WHERE ticker = ? AND date >= ?", (ticker, date_7))
    weekly = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM daily_whale WHERE ticker = ? AND date >= ?", (ticker, date_30))
    monthly = cursor.fetchone()[0]
    
    conn.close()
    return weekly, monthly

def save_whale_event(data):
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

# =========================================================
# 📊 Z-score 계산
# =========================================================
def calculate_z_score(ticker, today_vol):
    try:
        stock = yf.Ticker(ticker)
        # 통계적 신뢰도를 위해 1년치 데이터 사용
        hist = stock.history(period="1y")
        if len(hist) < 20: return 0.0
        
        # 오늘(최근) 데이터를 제외한 과거 데이터로 기준선 산출
        past_data = hist[:-1]
        mean_vol = past_data['Volume'].mean()
        std_vol = past_data['Volume'].std()
        
        if std_vol == 0: return 0.0
        return round((today_vol - mean_vol) / std_vol, 2)
    except:
        return 0.0

# =========================================================
# 🚀 메인 로직 (멀티 타겟 스캔)
# =========================================================
def run_whale_tracker():
    print("🐋 [Whale Tracker] S&P500 / Nasdaq100 / NYSE 정밀 감시 시작...")
    
    if not os.path.exists(DB_PATH):
        print("   ❌ DB 파일이 없습니다. 'init_whale_db.py'를 먼저 실행해주세요.")
        return []

    report_date = get_target_report_date()
    print(f"   📅 분석 기준일 확정: {report_date}")

    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    results = []
    
    # 중복 리포팅 방지용 (이미 처리한 종목은 건너뜀)
    seen_tickers = set()

    # 감시 대상 그룹 정의 (이름, Finviz 필터코드)
    # idx_sp500: S&P 500
    # idx_ndx: Nasdaq 100
    # exch_nyse: NYSE (거래소 전체)
    TARGETS = [
        ("S&P 500", "idx_sp500"),
        ("Nasdaq 100", "idx_ndx"),
        ("NYSE", "exch_nyse")
    ]

    for target_name, filter_code in TARGETS:
        print(f"\n   🔍 [{target_name}] 그룹 스캔 중... (Top 60)")
        
        # 각 그룹당 3페이지(60개) 스캔: 1, 21, 41
        for start_row in range(1, 61, 20):
            # 필터 조합: 해당지수 + 상대거래량 > 1.5 + 거래량 내림차순
            url = f"https://finviz.com/screener.ashx?v=111&f={filter_code},sh_relvol_o1.5&ft=4&o=-volume&r={start_row}"
            
            try:
                # print(f"      📡 Page {(start_row//20)+1} 요청 중...")
                res = requests.get(url, headers=headers, timeout=10)
                dfs = pd.read_html(res.text, header=0, attrs={'class': 'table-light'})
                
                if not dfs: break
                df = dfs[0]
                
                for index, row in df.iterrows():
                    try:
                        ticker = str(row['Ticker'])
                        
                        # 이미 분석한 종목이면 스킵 (중복 방지)
                        if ticker in seen_tickers:
                            continue
                        
                        seen_tickers.add(ticker) # 처리 목록에 추가

                        # 데이터 파싱
                        price = float(str(row['Price']))
                        rel_vol = float(str(row['Rel Volume']))
                        vol_str = str(row['Volume'])
                        if 'M' in vol_str: volume = int(float(vol_str.replace('M','')) * 1_000_000)
                        elif 'B' in vol_str: volume = int(float(vol_str.replace('B','')) * 1_000_000_000)
                        elif 'K' in vol_str: volume = int(float(vol_str.replace('K','')) * 1_000)
                        else: volume = int(vol_str)
                    except: continue

                    # ------------------------------------------
                    # 2차 검증: Z-score > 2.0
                    # ------------------------------------------
                    z_score = calculate_z_score(ticker, volume)
                    
                    if z_score >= 2.0:
                        # DB 저장
                        data = {
                            'ticker': ticker, 'date': report_date, 'price': price,
                            'volume': volume, 'z_score': z_score, 'rel_volume': rel_vol
                        }
                        save_whale_event(data)
                        
                        # 빈도 조회
                        weekly, monthly = get_frequency(ticker)
                        
                        # 그룹명 태그 추가 (어디서 발견됐는지)
                        results.append({
                            "ticker": ticker,
                            "group": target_name, # S&P 500 등
                            "date": report_date,
                            "price": f"${price}",
                            "volume": f"{volume:,}",
                            "z_score": z_score,
                            "rel_volume": rel_vol,
                            "weekly_freq": weekly,
                            "monthly_freq": monthly,
                            "msg": f"🔥 {ticker} ({target_name}): Z-score {z_score}"
                        })
                        print(f"      🚨 [포착] {ticker} (Z:{z_score}, 월간:{monthly}회)")
                
                time.sleep(1) # 페이지 넘길 때 딜레이

            except Exception as e:
                print(f"   ⚠️ 크롤링 에러 ({target_name}): {e}")
                break
    
    print(f"\n✅ 스캔 완료. 총 {len(results)}건의 고래 거래 포착.")
    return results