from datetime import datetime
import pytz
import os
from fastapi import APIRouter, Response
from fastapi.responses import HTMLResponse # Added for HTML responses
from services.briefing_market_index import get_market_summary_markdown, get_sp500_map_image 
from services.economy_indicators import get_economy_indicators
from services.market_news_crawl_llm import get_market_news
from services.email_builder import generate_email_report
from services.sentiment_analysis import get_sentiment_analysis
from services.stock_news import get_interested_stock_news
from services.whale_tracker import get_whale_tracker_data
from services.insider_tracker import get_insider_trades


router = APIRouter(
    prefix="/report",  # All routes in this router will be prefixed with /report
    tags=["Report"]
)

# 1-1. Endpoint to generate daily market indicators markdown
@router.post("/market-indicators")
def generate_market_indicators():
    markdown_table = get_market_summary_markdown()
    
    # Return as a JSON structure ready for n8n or frontend consumption
    return {
        "status": "success",
        "market_summary_markdown": markdown_table
    }

# 1-2. Endpoint to generate S&P 500 Map image (Base64)
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
            "message": "Failed to capture image"
        }
    
# 1-3. Endpoint to fetch FRED & Forex Factory economic indicators
@router.post("/economy-indicators")
def fetch_economy_indicators():
    """
    1-3. Crawls economic indicators from FRED and Forex Factory.
    """
    data = get_economy_indicators()
    return {
        "status": "success",
        "data": data 
    }

# 1-4. Endpoint to summarize major market-moving news (Upstage AI)
@router.post("/market-news")
def fetch_market_news():
    """
    1-4. Summarizes top 5 US stock market news from the past 24 hours using Upstage AI.
    """
    news_data = get_market_news()
    return {
        "status": "success",
        "data": news_data
    }

# 2-1. Endpoint for target stocks community sentiment analysis
@router.post("/sentiment-analysis")
def fetch_sentiment_analysis():
    """
    2-1. Analyzes community sentiment (Fear/Greed Index) for target stocks.
    """
    data = get_sentiment_analysis()
    return {
        "status": "success",
        "data": data
    }

# 2-2. Endpoint for target stock news collection
@router.post("/stock-news")
def fetch_stock_news():
    """
    2-2. Collects the latest news articles for Target Stocks.
    """
    news_data = get_interested_stock_news()
    return {
        "status": "success",
        "data": news_data
    }

# 3-1. Endpoint for whale appearance frequency analysis
@router.post("/whale-frequency")
def report_whale_frequency():
    """
    3-1. Identifies high-volume trade frequencies.
    [Whale Tracker]
    1. Scans Finviz for stocks with RelVol > 1.5
    2. Validates if Z-score >= 2.0
    3. Saves to DB and returns frequency analysis results
    """
    data = get_whale_tracker_data() 
    
    # [Fixed] Updated keys to match the new dictionary structure from whale_tracker.py
    total_count = len(data.get('stocks', [])) + len(data.get('etfs', []))
    
    return {
        "status": "success",
        "count": total_count,
        "data": data 
    }

# 3-2. Endpoint for insider trades analysis
@router.post("/insider-trades")
def report_insider_trades():
    """
    3-2. Analyzes recent significant insider trades and clusters.
    """
    data = get_insider_trades()
    
    # Calculate total count of significant trades
    total_count = len(data['cluster_buys']) + len(data['significant_buys']) + len(data['significant_sells'])
    
    return {
        "status": "success",
        "count": total_count,
        "data": data 
    }

# Final. Endpoint to return HTML email body
@router.post("/daily-briefing")
def get_daily_briefing():
    """
    Final. Generates and returns the compiled Daily Briefing email subject and HTML body.
    """
    try:
        html_content = generate_email_report()
        
        # [Added] Dynamically generate the email subject based on language
        lang_code = os.getenv("REPORT_LANGUAGE", "en").lower()
        kst_tz = pytz.timezone('Asia/Seoul')
        now_kst = datetime.now(kst_tz)
        
        if lang_code == 'ko':
            date_str = now_kst.strftime("%m월 %d일")
            subject = f"[FinSight] 🇺🇸 {date_str} 미국 증시 데일리 브리핑"
        else:
            date_str = now_kst.strftime("%b %d")
            subject = f"[FinSight] 🇺🇸 {date_str} Daily Market Briefing"

        # Return both Subject and HTML as a JSON object
        return {
            "status": "success",
            "subject": subject,
            "html": html_content
        }
        
    except Exception as e:
        print(f"❌ Server Error: {e}")
        return {
            "status": "error",
            "message": str(e)
        }