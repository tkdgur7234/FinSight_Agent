import os
import markdown
import re
from datetime import datetime, timedelta
import pytz 
from jinja2 import Environment, FileSystemLoader

# Feature Imports
from services.briefing_market_index import get_market_summary_markdown, get_sp500_map_image
from services.economy_indicators import get_economy_indicators
from services.market_news_crawl_llm import get_market_news
from services.sentiment_analysis import get_sentiment_analysis
from services.stock_news import get_interested_stock_news
from services.whale_tracker import get_whale_tracker_data
from services.insider_tracker import get_insider_trades

# ---------------------------------------------------------
# [i18n] UI Translation Dictionary for HTML Template
# ---------------------------------------------------------
UI_TEXT = {
    "en": {
        "title_main": "🇺🇸 US Market Daily Briefing",
        "toc": "📑 Table of Contents",
        "toc_summary": "Today's Key Summary",
        "toc_indices": "Major Indices",
        "toc_eco": "Economic Indicators",
        "toc_map": "S&P 500 Heatmap",
        "toc_news": "Wall Street Overnight News",
        "toc_watch": "Watchlist Monitoring",
        "toc_anomaly": "Anomaly Detection System",
        
        "sec_summary": "⚡ Today's Key Summary",
        "err_no_news": "No news data retrieved.",
        
        "sec_indices": "📊 Major Indices",
        "sec_eco": "📅 Economic Indicators",
        "eco_forecast": "Forecast",
        "eco_impact": "Impact",
        
        "sec_map": "🌎 S&P 500 Heatmap",
        
        "sec_news": "📰 Wall Street Overnight News",
        "news_summary": "Summary",
        
        "sec_watch": "👀 Watchlist Monitoring",
        "watch_sentiment": "🗣️ Community Sentiment (Reddit)",
        "sent_greed": "Greed",
        "sent_fear": "Fear",
        "sent_neutral": "Neutral",
        "watch_news": "🗞️ Latest News for Watchlist",
        
        "sec_anomaly": "🚨 Anomaly Detection System",
        "ano_whale": "🐋 Whale Alerts (Z-score ≥ 2.0)",
        "ano_whale_desc": "Detected via Finviz (Rel Volume) & statistical Z-score analysis. Indicates suspected institutional intervention beyond normal volatility. (Top 15)",
        "ano_normal": "📈 Rising Activity (Z-score < 2.0)",
        "ano_normal_desc": "Increased volume due to market interest/overbought conditions, but below the statistical anomaly threshold. (Top 10)",
        "tbl_ticker": "Ticker",
        "tbl_size": "Size",
        "tbl_sector": "Sector",
        "tbl_zscore": "Z-Score",
        "tbl_freq": "Freq (W/M)",
        "tbl_status": "Status",
        "ano_active_status": "Active",
        
        "ano_insider": "🕵️ Insider Trading Trends (Last 30 Days)",
        "ano_insider_desc": "Displays ticker, industry, and total transaction amount/role for cluster buys or major trades.",
        "insider_guide_title": "[Insider Trade Filtering Criteria]",
        "insider_g1": "• <b>1st Filter</b>: Open market buys/sells only. Penny stocks excluded (Price ≥ $3, Buy ≥ $150k, Sell ≥ $500k)",
        "insider_g2": "• <b>2nd Filter</b>: Financial health (Market Cap ≥ $250M, Daily Turnover ≥ $2.5M)",
        "insider_g3": "• <b>Categories</b>: Cluster Buy (3+ insiders within 30d), Significant Buy (≥ $150k & C-level), Big Sell (≥ $500k)",
        
        "ins_cluster": "👑 Cluster Buy",
        "ins_sig_buy": "💎 Significant Buy",
        "ins_big_sell": "📉 Big Sell",
        "tbl_industry": "Industry",
        "tbl_insider": "Insider (Role)",
        "tbl_amount": "Amount",
        
        "no_data": "No data captured.",
        
        "footer_disclaimer": "This report is AI-generated and should be used for informational purposes only.",
        "footer_contact": "Contact",
        "footer_copyright": "Created by SangHyeok Park | FinSight Agent ©"
    },
    "ko": {
        "title_main": "🇺🇸 미국 증시 데일리 브리핑",
        "toc": "📑 리포트 목차",
        "toc_summary": "오늘의 핵심 요약",
        "toc_indices": "주요 지수 현황",
        "toc_eco": "주요 경제 지표 발표",
        "toc_map": "S&P 500 히트맵",
        "toc_news": "간밤의 월스트리트 주요 뉴스",
        "toc_watch": "관심 종목 모니터링",
        "toc_anomaly": "이상 거래 감지 시스템",
        
        "sec_summary": "⚡ 오늘의 핵심 요약",
        "err_no_news": "뉴스 데이터를 가져오지 못했습니다.",
        
        "sec_indices": "📊 주요 지수 현황",
        "sec_eco": "📅 주요 경제 지표 발표",
        "eco_forecast": "예상",
        "eco_impact": "중요도",
        
        "sec_map": "🌎 S&P 500 히트맵",
        
        "sec_news": "📰 간밤의 월스트리트 주요 뉴스",
        "news_summary": "요약",
        
        "sec_watch": "👀 관심 종목 모니터링",
        "watch_sentiment": "🗣️ 커뮤니티 투자의견(Reddit)",
        "sent_greed": "탐욕",
        "sent_fear": "공포",
        "sent_neutral": "중립",
        "watch_news": "🗞️ 관심 종목 최신 뉴스",
        
        "sec_anomaly": "🚨 이상 거래 감지 시스템",
        "ano_whale": "🐋 대규모 거래 포착 (Z-score ≥ 2.0)",
        "ano_whale_desc": "Finviz(Rel Volume)로 1차 포착 후, 통계적 이상치(Z-score) 분석을 통해 단순 변동성을 넘어선 세력의 개입이 의심되는 종목입니다. (상위 15개)",
        "ano_normal": "📈 활동성 급증 종목 (Z-score < 2.0)",
        "ano_normal_desc": "단순 과매수 또는 시장 관심으로 인해 거래량이 평소보다 증가했으나, 통계적 이상 범위(2.0)에는 미치지 못하는 종목입니다. (상위 10개)",
        "tbl_ticker": "티커",
        "tbl_size": "규모",
        "tbl_sector": "섹터(대분류)",
        "tbl_zscore": "Z-Score",
        "tbl_freq": "빈도 (주/월)",
        "tbl_status": "상태",
        "ano_active_status": "활동성 증가",
        
        "ano_insider": "🕵️ 내부자 거래 동향 (최근 30일)",
        "ano_insider_desc": "티커, 분야(세부 산업), 그리고 집단 매수거나 주요 매수/매도일 경우 총 거래금과 직급을 명시합니다.",
        "insider_guide_title": "[내부자 거래 필터링 기준 안내]",
        "insider_g1": "• <b>1차 필터링</b>: 장내 매수, 단순 매도만 허용 / 동전주 방지 (주가 $3↑, 매수 $150k↑, 매도 $500k↑)",
        "insider_g2": "• <b>2차 필터링</b>: 재무 건전성 필터링 (시가총액 2500억↑, 일 거래대금 25억↑)",
        "insider_g3": "• <b>리포팅 분류</b>: 집단 매수 (최근 30일 내 3명 이상 매수), 주요 매수 ($150k 이상 매수 및 C-level), 대량 매도 ($400k 이상)",
        
        "ins_cluster": "👑 Cluster Buy (집단 매수)",
        "ins_sig_buy": "💎 Significant Buy (주요 매수)",
        "ins_big_sell": "📉 Big Sell (대량 매도)",
        "tbl_industry": "세부 산업",
        "tbl_insider": "내부자 (직급)",
        "tbl_amount": "거래금",
        
        "no_data": "포착된 내역이 없습니다.",
        
        "footer_disclaimer": "본 리포트는 AI에 의해 자동 생성되었으며, 투자의 참고 자료로만 활용하시기 바랍니다.",
        "footer_contact": "문의사항",
        "footer_copyright": "Created by SangHyeok Park | FinSight Agent ©"
    }
}

def generate_email_report():
    print("💌 [Email Builder] Starting report generation...")

    # Load Language Settings
    lang_code = os.getenv("REPORT_LANGUAGE", "en").lower()
    t = UI_TEXT.get(lang_code, UI_TEXT["en"])

    # [1-1] Index Table
    print("   -> Creating Index Table...")
    md_table = get_market_summary_markdown()
    html_table = markdown.markdown(md_table, extensions=['tables'])

    # Regex to prevent table layout breaking
    html_table = html_table.replace('<table>', '<table width="100%" cellpadding="0" cellspacing="0" style="width: 100%; border-collapse: collapse; font-size: 14px; margin-bottom: 15px;">')
    html_table = re.sub(r'<th[^>]*>', '<th style="padding: 10px; border-bottom: 1px solid #ddd; text-align: center; background-color: #f8f9fa; font-weight: bold; color: #2c3e50;">', html_table)
    html_table = re.sub(r'<td[^>]*>', '<td style="padding: 10px; border-bottom: 1px solid #ddd; text-align: center;">', html_table)
    html_table = re.sub(r'<tr[^>]*>', '<tr style="border-bottom: 1px solid #ddd;">', html_table)

    # [1-2] S&P 500 Map
    print("   -> Fetching Map Image...")
    sp500_img = get_sp500_map_image()

    # [1-3] Economy Data (Filter for yesterday)
    print("   -> Fetching Economy Data...")
    raw_economy_data = get_economy_indicators()
    
    kst_tz = pytz.timezone('Asia/Seoul')
    now_kst = datetime.now(kst_tz)
    yesterday_kst = now_kst - timedelta(days=1)
    target_date_str = yesterday_kst.strftime("%Y-%m-%d")

    economy_data = []
    if raw_economy_data:
        # Retrieve the key dynamically based on language
        filter_key = "필터링(전일 발표)" if lang_code == "ko" else "Filter (Prev Day)"
        for item in raw_economy_data:
            if item.get(filter_key) == target_date_str:
                economy_data.append(item)

    # [1-4] Market News
    print("   -> Crawling Market News...")
    news_result = get_market_news()
    
    if isinstance(news_result, dict):
        market_summary = news_result.get("market_summary", t["err_no_news"])
        news_list = news_result.get("news_list", [])
    else:
        market_summary = t["err_no_news"]
        news_list = []

    # [2-1 & 2-2] Watchlist
    print("   -> Fetching Watchlist Sentiment & News...")
    watchlist_sentiments = get_sentiment_analysis()
    watchlist_news = get_interested_stock_news()

    # [3-1 & 3-2] Anomaly Detection
    print("   -> Tracking Whale & Insider Trades...")
    raw_whale_data = get_whale_tracker_data()
    
    whale_list = raw_whale_data.get('whale_alerts', []) if raw_whale_data else []
    normal_list = raw_whale_data.get('normal_alerts', []) if raw_whale_data else []
    insider_trades = get_insider_trades()

    print("   ✅ Data collection complete! Starting HTML rendering...")

    # Jinja2 Template Load
    template_dir = os.path.join(os.path.dirname(__file__), '../templates')
    try:
        env = Environment(loader=FileSystemLoader(template_dir))
        template = env.get_template('report_template.html')
    except Exception as e:
        print(f"   ❌ Template Loading Error: {e}")
        return f"<h1>Template Error</h1><p>{str(e)}</p>"

    # Date Formatting
    if lang_code == 'ko':
        today_str = now_kst.strftime("%Y년 %m월 %d일 (%a)")
        year = now_kst.strftime("%Y")
    else:
        today_str = now_kst.strftime("%A, %b %d, %Y")
        year = now_kst.strftime("%Y")
    
    # Render with Data and Translation Dictionary
    rendered_html = template.render(
        t=t, # Passing the translation dictionary
        year=year,
        today_date=today_str,
        market_summary=market_summary,
        market_table_html=html_table,
        sp500_image=sp500_img,
        economy_list=economy_data,
        market_news_list=news_list,            
        watchlist_sentiments=watchlist_sentiments, 
        watchlist_news=watchlist_news,             
        whale_alerts=whale_list,                   
        normal_alerts=normal_list,                 
        insider_trades=insider_trades              
    )
    
    print("   ✅ Report successfully generated and rendered!")
    return rendered_html