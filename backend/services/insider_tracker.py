import cloudscraper
import pandas as pd
import io
import yfinance as yf  # [추가] 재무 데이터 확인용
from datetime import datetime
import time
import random

# ---------------------------------------------------------
# [Setup]
# pip install cloudscraper pandas yfinance
# ---------------------------------------------------------

# [설정: 사용자 요청 기준 (USD 환산)]
MIN_PRICE = 3.0                  # 주가 $3 이상 (약 4천원)
MIN_TRADE_VALUE = 150000         # 매수금액 $150k 이상 (약 2.1억)
MIN_MARKET_CAP = 250_000_000     # 시총 $250M 이상 (약 3,500억)
MIN_DAILY_TURNOVER = 2_500_000   # 일 거래대금 $2.5M 이상 (약 35억)

# [직급 가중치]
ROLE_WEIGHTS = {
    "CEO": 10, "Chief Executive Officer": 10,
    "CFO": 10, "Chief Financial Officer": 10,
    "COB": 8,  "Chairman": 8,
    "Pres": 7, "President": 7,
    "COO": 6,
    "Dir": 5,  "Director": 5,
    "VP": 4,   "Vice President": 4,
    "Officer": 3,
    "10%": 2   
}

C_LEVEL_TITLES = ["CEO", "Chief Executive Officer", "CFO", "Chief Financial Officer", "President"]

def log(msg):
    print(msg, flush=True)

def is_c_level(title):
    title_lower = str(title).lower()
    for c_title in C_LEVEL_TITLES:
        if c_title.lower() in title_lower:
            return True
    return False

def get_role_weight(titles):
    max_weight = 1
    titles_lower = str(titles).lower()
    for role, weight in ROLE_WEIGHTS.items():
        if role.lower() in titles_lower:
            max_weight = max(max_weight, weight)
    return max_weight

# [신규 기능] yfinance로 시총 및 거래대금 검증
def check_financial_health(ticker):
    try:
        # yfinance로 데이터 조회
        stock = yf.Ticker(ticker)
        info = stock.info
        
        # 1. 시가총액 (marketCap)
        market_cap = info.get('marketCap', 0)
        if market_cap < MIN_MARKET_CAP:
            return False, f"시총 미달 (${market_cap/1_000_000:.1f}M)"

        # 2. 거래대금 (Volume * Price)
        # avgVolume(3개월 평균) 사용하거나 volume(당일) 사용
        avg_vol = info.get('averageVolume', 0)
        curr_price = info.get('currentPrice', 0)
        
        # 데이터가 없을 경우 안전하게 0 처리
        if avg_vol is None: avg_vol = 0
        if curr_price is None: curr_price = 0
        
        daily_turnover = avg_vol * curr_price
        
        if daily_turnover < MIN_DAILY_TURNOVER:
            return False, f"거래대금 미달 (${daily_turnover/1_000_000:.1f}M)"

        return True, "Pass"

    except Exception as e:
        # yfinance 데이터가 없는 경우(ETF나 소형주 등)는 보수적으로 False 처리
        # 또는 정말 중요한 정보라면 True로 넘길 수도 있음. 여기선 엄격하게 False.
        return False, f"데이터 조회 불가 ({e})"

def get_insider_trades():
    log(f"🕵️‍♂️ [Insider Tracker] 정밀 분석 시작 (기준: 매수${MIN_TRADE_VALUE/1000}k+, 시총${MIN_MARKET_CAP/1000000}M+)")

    scraper = cloudscraper.create_scraper()
    base_url = "http://openinsider.com/screener"
    
    # 파라미터: 2000건, 최근 30일
    params = {
        'cnt': 2000,
        'ph': 1, 'isc': 1,
        'daysago': 30,
        'xp': 1, 'xs': 1,
        'sortcol': 0, 
    }

    try:
        log("   📡 OpenInsider 데이터 요청 중...")
        res = scraper.get(base_url, params=params)
        
        if res.status_code != 200:
            log(f"   ⚠️ 접속 실패 (Status: {res.status_code})")
            return empty_result()

        try:
            dfs = pd.read_html(io.StringIO(res.text), header=0)
        except ValueError:
            return empty_result()

        if not dfs: return empty_result()
        
        # 테이블 찾기
        df = None
        candidate_tables = []
        for table in dfs:
            clean_cols = [str(c).replace('\n', '').replace('\xa0', ' ').strip() for c in table.columns]
            table.columns = clean_cols
            if 'Ticker' in clean_cols and 'Trade Type' in clean_cols:
                candidate_tables.append(table)
        
        if not candidate_tables: return empty_result()
        df = max(candidate_tables, key=len)
        log(f"   ✅ 데이터 확보 완료 (총 {len(df)}건). 2차 정밀 필터링 시작...")

        # -----------------------------------------------------------
        # [분석 로직]
        # -----------------------------------------------------------
        cluster_candidates = {} 
        
        # 컬럼 매핑
        type_col = next((c for c in df.columns if 'Trade' in c and 'Type' in c), 'Trade Type')
        ticker_col = 'Ticker'
        insider_col = next((c for c in df.columns if 'Insider' in c), 'Insider Name')
        val_col = next((c for c in df.columns if 'Value' in c), 'Value')
        price_col = next((c for c in df.columns if 'Price' in c), 'Price')

        # 1. Cluster Buy 후보군 계산 (매수 행위 자체는 소액이어도 카운팅? -> 아니오, 이번엔 엄격하게)
        # 사용자 요청: "매수 금액도 2억 넘어야 함". 따라서 Cluster 후보도 2억 넘는 사람만 카운트.
        for _, row in df.iterrows():
            trade_type = str(row.get(type_col, ''))
            
            # Sale+OE 제외
            if 'Purchase' in trade_type and 'OE' not in trade_type:
                # 금액 확인
                val_str = str(row.get(val_col, '0')).replace('$','').replace(',','').replace('+','').strip()
                try: value = float(val_str)
                except: value = 0.0
                
                # [Cluster 조건 강화] 2억($150k) 넘는 매수만 "의미 있는 매수자"로 인정
                if value >= MIN_TRADE_VALUE:
                    ticker = str(row.get(ticker_col, ''))
                    insider = str(row.get(insider_col, ''))
                    if ticker not in cluster_candidates:
                        cluster_candidates[ticker] = set()
                    cluster_candidates[ticker].add(insider)

        # 2. 리포팅 (최신 100건)
        latest_df = df.head(100)
        
        cluster_buys = []
        significant_buys = []
        significant_sells = []
        
        # yfinance 캐싱 (중복 호출 방지)
        checked_tickers = {} # {ticker: bool}

        stats = {'oe': 0, 'penny': 0, 'small_money': 0, 'bad_financial': 0, 'valid': 0}

        for index, row in latest_df.iterrows():
            try:
                trade_type = str(row.get(type_col, ''))
                
                # [필터] Sale+OE 제거
                if 'OE' in trade_type: 
                    stats['oe'] += 1
                    continue

                # 값 파싱
                val_str = str(row.get(val_col, '0')).replace('$','').replace(',','').replace('+','').strip()
                try: value = float(val_str)
                except: value = 0.0

                price_str = str(row.get(price_col, '0')).replace('$','').strip()
                try: price = float(price_str)
                except: price = 0.0

                is_purchase = 'Purchase' in trade_type
                is_sale = 'Sale' in trade_type

                if not (is_purchase or is_sale): continue

                # [필터 1] 금액 필터 ($150k / $500k)
                min_val = MIN_TRADE_VALUE if is_purchase else MIN_SELL_VALUE_FILTER
                if value < min_val:
                    stats['small_money'] += 1
                    continue

                # [필터 2] 주가 필터 ($3 미만 제외)
                if price < MIN_PRICE:
                    stats['penny'] += 1
                    continue

                # [필터 3] 재무 건전성 (시총 & 거래대금) - yfinance
                # 매수(Buy)인 경우에만 엄격하게 체크 (매도는 탈출일 수 있으니 굳이 우량주 아니어도 됨)
                ticker = str(row.get(ticker_col, ''))
                
                if is_purchase:
                    if ticker not in checked_tickers:
                        # yfinance 호출 (약간의 딜레이 발생 가능)
                        is_healthy, reason = check_financial_health(ticker)
                        checked_tickers[ticker] = is_healthy
                        if not is_healthy:
                             # log(f"      [탈락] {ticker}: {reason}")
                             pass
                    
                    if not checked_tickers[ticker]:
                        stats['bad_financial'] += 1
                        continue

                # 데이터 정리
                insider = str(row.get(insider_col, ''))
                title_col = next((c for c in df.columns if 'Title' in c), 'Title')
                titles = str(row.get(title_col, ''))
                date_col = next((c for c in df.columns if 'Trade' in c and 'Date' in c), 'Trade Date')
                date_str = str(row.get(date_col, '')).split(' ')[0]

                role_weight = get_role_weight(titles)
                is_c_lvl = is_c_level(titles)

                # Cluster Check
                is_cluster = False
                buyer_count = 0
                if ticker in cluster_candidates:
                    buyer_count = len(cluster_candidates[ticker])
                    if buyer_count >= 3:
                        is_cluster = True

                trade_data = {
                    "ticker": ticker,
                    "insider": insider,
                    "role": titles,
                    "type": trade_type,
                    "date": date_str,
                    "price": f"${price}",
                    "value": value,
                    "value_str": f"${value:,.0f}",
                    "is_c_level": is_c_lvl,
                    "is_cluster": is_cluster,
                    "cluster_count": buyer_count
                }

                # 분류 및 담기
                if is_purchase:
                    stats['valid'] += 1
                    if is_cluster:
                        cluster_buys.append(trade_data)
                    else:
                        significant_buys.append(trade_data)
                elif is_sale:
                    stats['valid'] += 1
                    significant_sells.append(trade_data)

            except Exception as e:
                continue

        # 정렬
        cluster_buys.sort(key=lambda x: x['value'], reverse=True)
        significant_buys.sort(key=lambda x: x['value'], reverse=True)
        significant_sells.sort(key=lambda x: x['value'], reverse=True)

        log(f"✅ 분석 완료. 👑Cluster: {len(cluster_buys)}, 💎Buy: {len(significant_buys)}, 📉Sell: {len(significant_sells)}")
        log(f"   ℹ️ 필터링: 소액 {stats['small_money']}, 동전주 {stats['penny']}, 시총/거래량미달 {stats['bad_financial']}, 유효 {stats['valid']}")
        
        return {
            "cluster_buys": cluster_buys,
            "significant_buys": significant_buys,
            "significant_sells": significant_sells
        }

    except Exception as e:
        log(f"   ❌ 에러 발생: {e}")
        return empty_result()

def empty_result():
    return {"cluster_buys": [], "significant_buys": [], "significant_sells": []}

if __name__ == "__main__":
    get_insider_trades()