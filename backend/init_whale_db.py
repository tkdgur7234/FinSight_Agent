import sqlite3
import random
from datetime import datetime, timedelta
import numpy as np

# =========================================================
# ⚙️ [설정]
# =========================================================
DB_PATH = "whale_tracker.db"

# 가데이터 생성 대상 (주요 종목 위주로 생성)
MOCK_TICKERS = ["TSLA", "NVDA", "AAPL", "AMD", "MSFT", "PLTR", "SOFI", "AMZN", "GOOGL", "META"]

def init_db_and_mock_data():
    print("🗄️ [DB Setup] 데이터베이스 초기화 및 가데이터 생성 시작...")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. 테이블 생성 (스키마 정의)
    # PRIMARY KEY (ticker, date): 종목+날짜 조합은 유일해야 함 (중복 방지 핵심)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_whale (
            ticker TEXT,
            date TEXT,
            price REAL,
            volume INTEGER,
            z_score REAL,
            rel_volume REAL,
            is_whale_day BOOLEAN,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (ticker, date) 
        )
    ''')
    
    # 2. 1년치 가데이터(Mock Data) 생성
    # 오늘로부터 1년 전 ~ 어제까지
    end_date = datetime.now() - timedelta(days=1)
    start_date = end_date - timedelta(days=365)
    
    mock_count = 0
    current_date = start_date
    
    print(f"   📅 생성 기간: {start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')}")
    
    while current_date <= end_date:
        date_str = current_date.strftime('%Y-%m-%d')
        
        for ticker in MOCK_TICKERS:
            # 5% 확률로 고래 출몰일(Whale Day) 가정
            if random.random() < 0.05: 
                # 가짜 데이터 생성 (현실적인 범위 내 랜덤)
                fake_price = round(random.uniform(10, 200), 2)
                fake_vol = random.randint(1_000_000, 50_000_000)
                fake_z = round(random.uniform(2.0, 5.0), 2) # Z-score 2.0 이상
                fake_rvol = round(random.uniform(1.5, 4.0), 2) # RVOL 1.5 이상
                
                try:
                    cursor.execute('''
                        INSERT OR IGNORE INTO daily_whale 
                        (ticker, date, price, volume, z_score, rel_volume, is_whale_day)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (ticker, date_str, fake_price, fake_vol, fake_z, fake_rvol, 1))
                    mock_count += 1
                except sqlite3.Error as e:
                    print(f"Error: {e}")
        
        current_date += timedelta(days=1)
    
    conn.commit()
    conn.close()
    print(f"✅ DB 세팅 완료! (생성된 가데이터: {mock_count}건)")
    print(f"📂 생성된 파일: {DB_PATH}")

if __name__ == "__main__":
    init_db_and_mock_data()