import cloudscraper
import pandas as pd
import io
import yfinance as yf
from datetime import datetime, timedelta
import time
import random
from concurrent.futures import ThreadPoolExecutor, as_completed # [추가] 병렬 처리를 위함
from collections import defaultdict

# ---------------------------------------------------------
# [설정] 필터링 기준 (USD)
# ---------------------------------------------------------
LOOKBACK_DAYS = 30              

# Python 내부 정밀 필터 기준
MIN_PRICE = 3.0                 # 주가 $3 이상
MIN_MARKET_CAP = 250_000_000    # 시가총액 30억 이상
MIN_DAILY_TURNOVER = 2_500_000  # 일 거래대금 30억 이상

# [매매 금액 기준]
MIN_TRADE_VALUE_CLUSTER = 20000 # [cluster용] $2만 이상 매수에 내부자 3명 이상시 집단 매수로 인정
MIN_TRADE_VALUE_REPORT = 100000 # [주요 매수] $10만 이상 매수
MIN_SELL_VALUE_FILTER = 400000  # [매도] $400k 이상 매도만 감시

# [병렬 처리 설정]
MAX_WORKERS = 10 # 동시에 10개 종목씩 조회 (너무 높으면 차단될 수 있음)

C_LEVEL_TITLES = ["CEO", "Chief Executive Officer", "CFO", "Chief Financial Officer", "President"]

def log(msg):
    print(msg, flush=True)

def is_c_level(title):
    title_lower = str(title).lower()
    for c_title in C_LEVEL_TITLES:
        if c_title.lower() in title_lower: return True
    return False

# [재무 검증 - 단일 종목용]
def check_financial_health_single(ticker):
    try:
        stock = yf.Ticker(ticker)
        
        # 네트워크 호출 발생 (가장 시간 많이 먹는 부분)
        try:
            info = stock.fast_info
            market_cap = info.market_cap
        except:
            return ticker, False, "데이터 조회 불가", "N/A"

        if market_cap is None or market_cap < MIN_MARKET_CAP:
            return ticker, False, "시총 미달", "N/A"

        try:
            last_vol = info.last_volume
            last_price = info.last_price
            if last_vol is None: last_vol = 0
            if last_price is None: last_price = 0
            
            turnover = last_vol * last_price
            if turnover < MIN_DAILY_TURNOVER:
                return ticker, False, "거래대금 미달", "N/A"
        except:
            return ticker, False, "거래정보 없음", "N/A"

        # [수정] 통과한 종목에 한해 섹터(Sector) 정보 추가
        try:
            industry = stock.info.get('industry', 'N/A')
        except:
            industry = 'N/A'

        return ticker, True, "Pass", industry
    except Exception:
        return ticker, False, "조회 에러", "N/A"

def get_insider_trades():
    cutoff_date = datetime.now() - timedelta(days=LOOKBACK_DAYS)
    log(f"🕵️‍♂️ [Insider Tracker] 정밀 분석 시작 (병렬 처리 모드)...")
    
    scraper = cloudscraper.create_scraper()
    base_url = "http://openinsider.com/screener"
    
    # Raw Data 요청
    params = {'cnt': 5000, 'xp': 1, 'xs': 1, 'isc': 1}

    try:
        log("   📡 OpenInsider 데이터 요청 중...")
        res = scraper.get(base_url, params=params)
        
        if res.status_code != 200:
            log(f"   ⚠️ 접속 실패: {res.status_code}")
            return empty_result()

        try:
            dfs = pd.read_html(io.StringIO(res.text), header=0)
        except ValueError: return empty_result()

        if not dfs: return empty_result()
        
        # 테이블 찾기
        candidate_tables = []
        for table in dfs:
            clean_cols = [str(c).replace('\n', '').replace('\xa0', ' ').strip() for c in table.columns]
            table.columns = clean_cols
            if 'Ticker' in clean_cols and 'Trade Type' in clean_cols:
                candidate_tables.append(table)
        
        if not candidate_tables: return empty_result()
        df = max(candidate_tables, key=len)
        log(f"   ✅ 원본 데이터 확보 (총 {len(df)}건).")

        # 날짜 필터링
        date_col = next((c for c in df.columns if 'Filing' in c and 'Date' in c), 'Filing Date')
        df['dt_parsed'] = pd.to_datetime(df[date_col], errors='coerce')
        df = df.sort_values(by='dt_parsed', ascending=False)
        df_filtered = df[df['dt_parsed'] >= cutoff_date].copy()
        
        if df_filtered.empty:
            log("   ⚠️ 최근 데이터 없음.")
            return empty_result()

        newest = df_filtered['dt_parsed'].max().strftime('%Y-%m-%d')
        oldest = df_filtered['dt_parsed'].min().strftime('%Y-%m-%d')
        log(f"   📅 [기간 확정] {newest} ~ {oldest} (총 {len(df_filtered)}건)")

        # -----------------------------------------------------------
        # [Step 1] 1차 필터링 및 재무 검증 대상(Target) 식별
        # -----------------------------------------------------------
        log("   🔍 1차 필터링 및 재무 검증 대상 추출 중...")
        
        type_col = next((c for c in df.columns if 'Trade' in c and 'Type' in c), 'Trade Type')
        ticker_col = 'Ticker'
        insider_col = next((c for c in df.columns if 'Insider' in c), 'Insider Name')
        val_col = next((c for c in df.columns if 'Value' in c), 'Value')
        price_col = next((c for c in df.columns if 'Price' in c), 'Price')

        # Cluster 후보 계산
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

        # 재무 검사가 필요한 '유니크한' 티커 목록 추출
        tickers_to_check = set()
        
        # 1차 필터링을 미리 시뮬레이션해서 yfinance 호출할 놈만 추림
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

            # 1차 필터: 동전주 / 금액 미달
            if price < MIN_PRICE: continue
            
            # 금액 체크
            ticker = str(row.get(ticker_col, ''))
            is_cluster = False
            if ticker in cluster_candidates and len(cluster_candidates[ticker]) >= 3:
                is_cluster = True

            pass_value = False
            if is_sale:
                if value >= MIN_SELL_VALUE_FILTER: pass_value = True
            else: # 매수
                if is_cluster:
                    if value >= MIN_TRADE_VALUE_CLUSTER: pass_value = True
                else:
                    title_col = next((c for c in df.columns if 'Title' in c), 'Title')
                    is_c_lvl = is_c_level(str(row.get(title_col, '')))
                    if value >= MIN_TRADE_VALUE_REPORT or is_c_lvl: pass_value = True
            
            # 1차 통과 + 매수 포지션인 경우에만 재무 확인 필요
            if pass_value and is_purchase:
                tickers_to_check.add(ticker)

        log(f"   ⚡ 재무 검증이 필요한 종목: {len(tickers_to_check)}개 (병렬 처리 시작)")

        # -----------------------------------------------------------
        # [Step 2] 병렬 처리 (Multi-threading)로 재무 데이터 일괄 수집
        # -----------------------------------------------------------
        financial_cache = {} # {ticker: (is_healthy, sector)} [수정] 섹터 정보 추가 캐싱
        
        if tickers_to_check:
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                # 작업 제출
                future_to_ticker = {executor.submit(check_financial_health_single, t): t for t in tickers_to_check}
                
                # 결과 수집
                completed_cnt = 0
                for future in as_completed(future_to_ticker):
                    # [수정] 섹터 정보까지 함께 리턴받아 캐시에 저장
                    ticker, is_healthy, reason, sector = future.result()
                    financial_cache[ticker] = (is_healthy, sector)
                    
                    completed_cnt += 1
                    if completed_cnt % 10 == 0:
                        print(f"      ... 재무 데이터 수집 중 ({completed_cnt}/{len(tickers_to_check)}) ...", flush=True)

        log("   ✅ 재무 데이터 수집 완료. 최종 리포트 생성 중...")

        # -----------------------------------------------------------
        # [Step 3] 최종 조립 (메인 루프)
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

                # 금액 필터
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

                # 재무 필터 (캐시 사용)
                industry = "N/A" # [수정] 변수명을 sector에서 industry로 변경
                if is_purchase:
                    # 캐시에서 건강상태와 세부 산업(industry)을 함께 꺼냄
                    cache_data = financial_cache.get(ticker, (False, "N/A"))
                    is_healthy = cache_data[0]
                    industry = cache_data[1]
                    
                    if not is_healthy:
                        stats['skip_fin'] += 1
                        continue

                stats['valid'] += 1

                # 데이터 담기
                insider = str(row.get(insider_col, ''))
                title_col = next((c for c in df.columns if 'Title' in c), 'Title')
                titles = str(row.get(title_col, ''))
                date_str = row['dt_parsed'].strftime('%Y-%m-%d')
                is_c_lvl = is_c_level(titles)

                # [수정] 금액 포맷팅 최적화 ($1.2M 또는 $150k 형태)
                if value >= 1_000_000:
                    amount_str_formatted = f"${value/1_000_000:.1f}M"
                else:
                    amount_str_formatted = f"${int(value/1000)}k"

                trade_data = {
                    "ticker": ticker,
                    "industry": industry,     # [수정] HTML 매핑용 키값을 industry로 변경
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

        # 1. 집단 매수(Cluster Buys)를 티커별로 그룹화
        cluster_groups = defaultdict(list)
        for trade in cluster_buys:
            cluster_groups[trade['ticker']].append(trade)

        # 2. 그룹별 총 거래 금액 계산 및 내부 정렬
        cluster_summary = []
        for ticker, trades in cluster_groups.items():
            total_val = sum(t['value'] for t in trades) # 티커별 총합
            trades.sort(key=lambda x: x['value'], reverse=True) # 그룹 내 개별 정렬
            cluster_summary.append({
                'ticker': ticker,
                'total_val': total_val,
                'trades': trades
            })

        # 3. 그룹 총액(total_val) 기준으로 티커 그룹 정렬
        cluster_summary.sort(key=lambda x: x['total_val'], reverse=True)

        # 4. 정렬된 그룹을 다시 리스트로 평탄화 (HTML 렌더링용)
        sorted_cluster_buys = []
        for group in cluster_summary:
            sorted_cluster_buys.extend(group['trades'])

        cluster_buys = sorted_cluster_buys
        
        # 주요 매수 및 대량 매도는 기존처럼 개별 거래금액 기준으로 정렬
        significant_buys.sort(key=lambda x: x['value'], reverse=True)
        significant_sells.sort(key=lambda x: x['value'], reverse=True)

        log(f"✅ 분석 완료. 👑Cluster: {len(cluster_buys)}, 💎Buy: {len(significant_buys)}, 📉Sell: {len(significant_sells)}")
        log(f"📊 상세 필터링 통계:")
        log(f"   - 🟡 옵션 행사 (OE): {stats['skip_oe']}")
        log(f"   - 🪙 동전주 (<${MIN_PRICE}): {stats['skip_penny']}")
        log(f"   - 💸 금액 미달: {stats['skip_amt']}")
        log(f"   - 📉 재무 미달: {stats['skip_fin']}")
        log(f"   - ✅ 최종 통과: {stats['valid']}")
        
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