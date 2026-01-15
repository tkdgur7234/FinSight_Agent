from fastapi import APIRouter, Response
from services.briefing_market_index import get_market_summary_markdown, get_sp500_map_image 
from services.economy_indicators import get_economy_indicators
from services.market_news_crawl_llm import get_market_news
from services.email_builder import generate_email_report
from services.sentiment_analysis import get_sentiment_analysis
from services.stock_news import get_interested_stock_news
from services.abnormal_trade import detect_whale_trades, detect_insider_trading

router = APIRouter(
    prefix="/report",  # 이 라우터의 모든 주소 앞에 /report가 붙음
    tags=["Report"]
)

# 1-1. 각종 지표 데일리 시황 마크다운 생성 엔드포인트
@router.post("/market-indicators")
def generate_market_indicators():
    markdown_table = get_market_summary_markdown()
    
    # n8n이 바로 쓸 수 있는 JSON 구조로 리턴
    return {
        "status": "success",
        "market_summary_markdown": markdown_table
    }

router = APIRouter(
    prefix="/report",
    tags=["Report"]
)

# 1-2. S&P 500 Map 이미지(Base64) 생성 엔드포인트
@router.post("/sp500-map")
def fetch_sp500_map():
    img_base64 = get_sp500_map_image()
    
    if img_base64:
        return {
            "status": "success",
            "image_type": "base64",
            "image_data": img_base64
        }
    else:
        return {
            "status": "error", 
            "message": "이미지 캡처 실패"
        }
    
# 1-3. FRED & Forex Factory 경제 지표 크롤링 엔드포인트
@router.post("/economy-indicators")
def fetch_economy_indicators():
    """
    1-3. FRED & Forex Factory 경제 지표 크롤링
    """
    data = get_economy_indicators()
    return {
        "status": "success",
        "data": data 
    }

# 1-4. 전날 시장에 영향을 끼친 주요 뉴스들 요약 정리 (Upstage AI)
@router.post("/market-news")
def fetch_market_news():
    """
    1-4. 지난 24시간 주요 미국 증시 뉴스 5선 (Upstage AI 요약)
    """
    news_data = get_market_news()
    return {
        "status": "success",
        "data": news_data
    }

# 2-1. 관심 종목 커뮤니티 감성 분석 (공포/탐욕 지수) 엔드포인트
@router.post("/sentiment-analysis")
def fetch_sentiment_analysis():
    """
    2-1. 관심 종목 커뮤니티 감성 분석 (공포/탐욕 지수)
    """
    data = get_sentiment_analysis()
    return {
        "status": "success",
        "data": data
    }

# 2-2. 관심 종목 뉴스 수집 엔드포인트
@router.post("/stock-news")
def fetch_stock_news():
    """
    2-2. 관심 종목(Target Stocks) 관련 최신 뉴스 수집
    """
    news_data = get_interested_stock_news()
    return {
        "status": "success",
        "data": news_data
    }

# 3-1. 대규모 거래 탐지 엔드포인트
@router.post("/whale-watch")
def report_whale_trades():
    # 인자 없이 호출하면 파일 상단의 INTEREST_STOCKS 사용
    data = detect_whale_trades() 
    return {
        "status": "success", 
        "count": len(data), "data": data
    }

# 3-2. 내부자 거래 탐지 엔드포인트
@router.post("/insider-watch")
def report_insider_trades():
    # 인자 없이 호출하면 파일 상단의 TARGET_INSIDER_TICKERS (Nasdaq+SNP) 사용
    data = detect_insider_trading()
    return {
        "status": "success", 
        "count": len(data), "data": data
    }

# 최종. 모든 데이터를 취합하여 완성된 HTML 이메일 본문 반환 엔드포인트
@router.post("/daily-briefing")
def get_daily_briefing_html():
    try:
        html_content = generate_email_report()
        return Response(content=html_content, media_type="text/html")
    except Exception as e:
        # 서버 에러 로그를 명확히 보기 위해 print 추가
        print(f"❌ Server Error: {e}")
        return Response(content=f"<h1>Server Error</h1><p>{str(e)}</p>", status_code=500)