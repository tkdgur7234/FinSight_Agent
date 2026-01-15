import requests
import pandas as pd
import numpy as np
import os
import json
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

FMP_API_KEY = os.getenv("FMP_API_KEY")
BASE_URL = "https://financialmodelingprep.com/api/v3"

# =========================================================
# ⚙️ [설정] 감시 대상 종목 리스트
# =========================================================

# 1. 관심 종목 (대규모 거래 감시용)
INTEREST_STOCKS = ["TSLA", "RKLB", "PLTR", "SOFI", "IONQ"]

# 2. S&P 500 상위 20개
SNP_TOP_20 = [
    "AAPL"
]

# 3. NASDAQ 100
NASDAQ_100 = [
    "AAPL", "MSFT", "NVDA"
]
TARGET_INSIDER_TICKERS = list(set(SNP_TOP_20 + NASDAQ_100))


# =========================================================
# 🛠️ 유틸리티 함수
# =========================================================

def get_20day_avg_volume(ticker):
    """
    최근 20일 평균 거래량 조회
    1차 시도: historical-price-full (일봉 데이터 직접 계산)
    2차 시도: quote (API 제공 평균값 사용)
    """
    # 1차 시도
    url = f"{BASE_URL}/historical-price-full/{ticker}?timeseries=25&apikey={FMP_API_KEY}"
    try:
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            data = res.json()
            if 'historical' in data and data['historical']:
                df = pd.DataFrame(data['historical'])
                return df['volume'].head(20).mean()
    except Exception as e:
        print(f"   ⚠️ [{ticker}] 1차 평균 거래량 조회 실패: {e}")

    # 2차 시도 (Fallback)
    try:
        url_quote = f"{BASE_URL}/quote/{ticker}?apikey={FMP_API_KEY}"
        res = requests.get(url_quote, timeout=5)
        if res.status_code == 200:
            data = res.json()
            if data and isinstance(data, list):
                vol = data[0].get('avgVolume', 0)
                # print(f"   🔄 [{ticker}] 2차 시도(Quote) 성공: {vol}")
                return vol
    except Exception as e:
        print(f"   ❌ [{ticker}] 2차 평균 거래량 조회 실패: {e}")
    
    return 0

def get_role_weight(role_str):
    role = role_str.lower()
    if any(x in role for x in ['ceo', 'cfo', 'president', 'chairman']): return 3
    elif 'director' in role or 'vp' in role or 'officer' in role: return 2
    else: return 1

# =========================================================
# 3-1. 관심 종목 대규모 거래 감시 (Whale Monitoring)
# =========================================================

def detect_whale_trades(tickers=None):
    if tickers is None: tickers = INTEREST_STOCKS
    
    print(f"🐋 [3-1] 대규모 거래(Whale) 감시 시작 ({len(tickers)}종목)...")
    results = []

    for ticker in tickers:
        try:
            # 1. 평균 거래량 조회
            avg_vol = get_20day_avg_volume(ticker)
            if avg_vol == 0: 
                print(f"   ⚠️ [{ticker}] 평균 거래량을 가져올 수 없어 스킵합니다.")
                continue

            # 기준: 5분 거래량이 일평균의 1% 이상
            threshold = avg_vol * 0.01 
            
            # 2. 5분봉 데이터 조회
            url = f"{BASE_URL}/historical-chart/5min/{ticker}?apikey={FMP_API_KEY}"
            res = requests.get(url, timeout=10)
            candles = res.json()
            
            if not candles: 
                continue
            
            # [수정] 가장 최근 거래일(Last Trading Day) 데이터만 필터링
            # FMP는 최신순 정렬이므로 0번째 데이터의 날짜가 가장 최근 거래일임
            last_date_str = candles[0]['date'].split(' ')[0] # YYYY-MM-DD
            
            # 해당 날짜 데이터만 추출
            todays_candles = [c for c in candles if c['date'].startswith(last_date_str)]
            
            df = pd.DataFrame(todays_candles)
            whale_moves = []
            
            for i, row in df.iterrows():
                vol = row['volume']
                
                if vol >= threshold:
                    price_open = row['open']
                    price_close = row['close']
                    
                    move_type = "⚪ 중립"
                    marker = ""
                    
                    if price_close >= price_open:
                        move_type = "매집 (Accumulation)"
                        marker = "🔴" 
                    else:
                        move_type = "덤핑 (Dumping)"
                        marker = "🔵" 
                        
                    trade_time = row['date'].split(' ')[1] # HH:MM:SS
                    
                    whale_moves.append({
                        "time": trade_time,
                        "volume": f"{int(vol):,}",
                        "ratio": f"{round((vol/avg_vol)*100, 1)}%",
                        "price": f"${price_close}",
                        "type": move_type,
                        "marker": marker
                    })
            
            if whale_moves:
                # 시간순 정렬 (아침 -> 장마감)
                whale_moves.sort(key=lambda x: x['time'])
                
                results.append({
                    "ticker": ticker,
                    "date": last_date_str, # 분석한 날짜 표시
                    "avg_volume": f"{int(avg_vol):,}",
                    "trades": whale_moves
                })
                print(f"   -> {ticker}: {last_date_str} 기준 대규모 거래 {len(whale_moves)}건 포착")
                
        except Exception as e:
            print(f"   Error checking {ticker}: {e}")
            continue

    return results

# =========================================================
# 3-2. 주요 종목 내부자 거래 감시 (Insider Trading)
# =========================================================

def detect_insider_trading(tickers=None):
    if tickers is None: tickers = TARGET_INSIDER_TICKERS

    print(f"🕵️ [3-2] 내부자 거래 감시 시작 ({len(tickers)}종목)...")
    results = []
    
    CUTOFF_DATE = datetime(2025, 1, 1)

    for ticker in tickers:
        try:
            url = f"{BASE_URL}/insider-trading/{ticker}?limit=30&apikey={FMP_API_KEY}"
            res = requests.get(url, timeout=10)
            data = res.json()
            
            if not data: continue
            
            recent_trades = []
            unique_buyers = set()
            unique_sellers = set()

            for trade in data:
                # [수정] 데이터 타입 안전장치 추가
                if not isinstance(trade, dict):
                    # print(f"   ⚠️ [{ticker}] 잘못된 데이터 형식: {trade}")
                    continue

                # 1. 날짜 필터링
                trans_date_str = trade.get('transactionDate', '1900-01-01')
                try:
                    trans_date = datetime.strptime(trans_date_str, "%Y-%m-%d")
                except:
                    continue
                    
                if trans_date < CUTOFF_DATE: continue
                
                # 2. 매수/매도 구분
                t_type = trade.get('acquistionOrDisposition', '').upper()
                desc = trade.get('transactionType', '').lower()
                
                if any(x in desc for x in ['grant', 'award', 'gift', 'option']):
                    continue

                securities = trade.get('securitiesTransacted', 0)
                price = trade.get('price', 0)
                amount = securities * price
                
                if amount < 10000: continue 

                person_name = trade.get('reportingName', 'Unknown')
                role_weight = get_role_weight(trade.get('typeOfOwner', ''))
                
                trade_info = {
                    "date": trans_date_str,
                    "name": person_name,
                    "role": trade.get('typeOfOwner', 'Insider'),
                    "amount_val": amount, 
                    "amount_str": f"${int(amount):,}",
                    "price": f"${price}",
                    "weight": role_weight
                }

                if t_type == 'A' or 'buy' in desc:
                    trade_info['type'] = "Buy"
                    trade_info['marker'] = "🔴"
                    unique_buyers.add(person_name)
                    recent_trades.append(trade_info)

                elif t_type == 'D' or 'sell' in desc:
                    if 'exercise' not in desc:
                        trade_info['type'] = "Sell"
                        trade_info['marker'] = "🔵"
                        unique_sellers.add(person_name)
                        recent_trades.append(trade_info)

            if recent_trades:
                signal_labels = []
                if len(unique_buyers) >= 3:
                    signal_labels.append("🔥 Cluster Buy (3인이상 매수)")
                if len(unique_sellers) >= 3:
                    signal_labels.append("❄️ Cluster Sell (3인이상 매도)")
                
                recent_trades.sort(key=lambda x: (x['weight'], x['amount_val']), reverse=True)
                final_signal = ", ".join(signal_labels) if signal_labels else "Normal"
                
                overall_marker = ""
                if "Buy" in final_signal: overall_marker = "🔴"
                if "Sell" in final_signal: overall_marker = "🔵"

                results.append({
                    "ticker": ticker,
                    "signal": final_signal,
                    "marker": overall_marker,
                    "buyer_count": len(unique_buyers),
                    "seller_count": len(unique_sellers),
                    "trades": recent_trades[:5] 
                })
                print(f"   -> {ticker}: {len(recent_trades)}건 / Buyers:{len(unique_buyers)}, Sellers:{len(unique_sellers)}")

        except Exception as e:
            # print(f"   Error inside {ticker}: {e}")
            continue

    return results