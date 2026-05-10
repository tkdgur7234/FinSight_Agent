import os
import markdown
from datetime import datetime, timedelta
import pytz # 시간대 처리를 위해 추가
from jinja2 import Environment, FileSystemLoader

# 1번 기능 Imports
from services.briefing_market_index import get_market_summary_markdown, get_sp500_map_image
from services.economy_indicators import get_economy_indicators
from services.market_news_crawl_llm import get_market_news

# 2, 3번 기능 Imports 추가
from services.sentiment_analysis import get_sentiment_analysis
from services.stock_news import get_interested_stock_news
from services.whale_tracker import get_whale_tracker_data
from services.insider_tracker import get_insider_trades

def generate_email_report():
    print("💌 리포트 생성 시작...")

    # [1-1] 지수 테이블
    print("Creating Index Table...")
    md_table = get_market_summary_markdown()
    html_table = markdown.markdown(md_table, extensions=['tables'])

    # [1-2] S&P 500 맵
    print("Fetching Map Image...")
    sp500_img = get_sp500_map_image()

    # [1-3] 경제 지표 (전일 발표분만 필터링)
    print("Fetching Economy Data...")
    raw_economy_data = get_economy_indicators()
    
    # 한국 시간 기준 '어제' 날짜 구하기
    kst_tz = pytz.timezone('Asia/Seoul')
    now_kst = datetime.now(kst_tz)
    yesterday_kst = now_kst - timedelta(days=1)
    target_date_str = yesterday_kst.strftime("%Y-%m-%d")
    
    print(f"Filtering Economy Data for: {target_date_str}")

    economy_data = []
    if raw_economy_data:
        for item in raw_economy_data:
            if item.get("필터링(전일 발표)") == target_date_str:
                economy_data.append(item)

    # [1-4] 일반 시장 뉴스
    print("Crawling News...")
    news_result = get_market_news()
    
    if isinstance(news_result, dict):
        market_summary = news_result.get("market_summary", "요약 정보 없음")
        news_list = news_result.get("news_list", [])
    else:
        market_summary = "뉴스 데이터를 가져오지 못했습니다."
        news_list = []

    # --- [추가] 2번 기능: 관심 종목 모니터링 ---
    print("Fetching Sentiment Analysis...")
    watchlist_sentiments = get_sentiment_analysis()

    print("Fetching Stock News...")
    watchlist_news = get_interested_stock_news()

    # --- [추가] 3번 기능: 이상 거래 감지 ---
    print("Tracking Whale Trades...")
    raw_whale_data = get_whale_tracker_data()
    
    # HTML 템플릿 구조에 맞게 stocks와 etfs를 하나의 리스트로 통합
    whale_list = []
    if raw_whale_data:
        whale_list.extend(raw_whale_data.get('stocks', []))
        whale_list.extend(raw_whale_data.get('etfs', []))

    print("Tracking Insider Trades...")
    insider_trades = get_insider_trades()

    print("✅ 데이터 수집 완료! HTML 렌더링 시작...")

    # Jinja2 템플릿 로드
    template_dir = os.path.join(os.path.dirname(__file__), '../templates')
    
    try:
        env = Environment(loader=FileSystemLoader(template_dir))
        template = env.get_template('report_template.html')
    except Exception as e:
        print(f"❌ Template Loading Error: {e}")
        return f"<h1>Template Error</h1><p>{str(e)}</p>"

    # 렌더링
    today_str = now_kst.strftime("%Y년 %m월 %d일 (%a)") # KST 기준 날짜 표시
    
    # HTML 템플릿의 {{ 변수명 }}과 정확히 일치하도록 매핑
    rendered_html = template.render(
        today_date=today_str,
        market_summary=market_summary,
        market_table_html=html_table,
        sp500_image=sp500_img,
        economy_list=economy_data,
        market_news_list=news_list,           # HTML의 {{ market_news_list }}에 대응
        watchlist_sentiments=watchlist_sentiments, # 2-1
        watchlist_news=watchlist_news,             # 2-2
        whale_alerts=whale_list,                   # 3-1
        insider_trades=insider_trades              # 3-2
    )
    
    print("✅ 리포트 생성 및 렌더링 완료!")
    return rendered_html