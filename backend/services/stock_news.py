import feedparser
import re
import os
import json
from html import unescape
from datetime import datetime, timedelta
from dateutil import parser as date_parser
from difflib import SequenceMatcher
import pytz
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# =========================================================
# ▼▼▼ [Configuration] Target Stock News List ▼▼▼
# =========================================================
TARGET_STOCKS = [
    {
        "ticker": "TSLA",
        "name": "Tesla",
        "lang": "en",
        "limit": 2
    },
    {
        "ticker": "GOOG",
        "name": "Google",
        "lang": "en",
        "limit": 2
    }
]

# =========================================================
# ▼▼▼ [Configuration] Paywalled News Sources Blacklist ▼▼▼
# =========================================================
PAYWALLED_SOURCES = [
    "Bloomberg",
    "The Wall Street Journal",
    "Financial Times",
    "Barron's",
    "The Information",
    "Seeking Alpha",
    "The Economist",
    "Business Insider",
    "MarketWatch",
    "Hankyung", 
    "Maeil Business Newspaper"
]

def clean_html(raw_html):
    """Removes HTML tags from the string."""
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    return unescape(cleantext).strip()

def is_similar(a, b, threshold=0.5):
    """Checks similarity between two headlines."""
    return SequenceMatcher(None, a, b).ratio() > threshold

def is_paywalled(source_name):
    """Returns True if the news source is in the paywall blacklist."""
    if not source_name: return False
    source_lower = source_name.lower()
    for blocked in PAYWALLED_SOURCES:
        if blocked.lower() in source_lower:
            return True
    return False

def get_google_news_rss(query, lang="en", limit=2):
    """
    Fetches news from Google News RSS with advanced filtering.
    """
    
    # Statistics tracker
    stats = {
        "total_fetched": 0,
        "dropped_paywall": 0,
        "dropped_time": 0,
        "dropped_dup": 0,
        "accepted": 0
    }
    
    fetch_count = 15
    
    if lang == 'ko':
        rss_url = f"https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"
    else:
        rss_url = f"https://news.google.com/rss/search?q={query}+when:24h&hl=en-US&gl=US&ceid=US:en"

    try:
        feed = feedparser.parse(rss_url)
        news_results = []
        seen_titles = [] 
        
        kst_tz = pytz.timezone('Asia/Seoul')
        now_kst = datetime.now(kst_tz)
        cutoff_end = now_kst
        cutoff_start = now_kst - timedelta(hours=24) 

        stats["total_fetched"] = len(feed.entries)

        for entry in feed.entries:
            if len(news_results) >= limit:
                break
                
            if len(seen_titles) >= fetch_count * 2:
                break

            # [Filter 1] Paywall Check
            source_name = entry.source.title if 'source' in entry else ""
            if is_paywalled(source_name):
                stats["dropped_paywall"] += 1
                continue

            # [Filter 2] Time Precision Filter
            pub_date_str = entry.published if 'published' in entry else ""
            try:
                dt_obj = date_parser.parse(pub_date_str)
                if dt_obj.tzinfo is None:
                    dt_obj = dt_obj.replace(tzinfo=pytz.utc)
                article_dt_kst = dt_obj.astimezone(kst_tz)
                
                if not (cutoff_start <= article_dt_kst <= cutoff_end):
                    stats["dropped_time"] += 1
                    continue 
            except Exception:
                continue 

            # [Filter 3] Deduplication
            title = entry.title
            is_dup = False
            for seen in seen_titles:
                if is_similar(title, seen):
                    is_dup = True
                    break
            
            if is_dup:
                stats["dropped_dup"] += 1
                continue 
            
            seen_titles.append(title)
            stats["accepted"] += 1
            
            pub_date_fmt = article_dt_kst.strftime("%Y-%m-%d %H:%M")
            
            news_results.append({
                "title": entry.title,
                "link": entry.link,
                "pub_date": pub_date_fmt,
                "source": source_name or "Google News"
            })
            
        if stats["accepted"] == 0:
            print(f"   ⚠️ [Result] '{query}' collected 0 items! (Reasons: Time={stats['dropped_time']}, Paywall={stats['dropped_paywall']}, Dup={stats['dropped_dup']})")
            
        return news_results

    except Exception as e:
        print(f"RSS Error ({query}): {e}")
        return []

def analyze_news_sentiment(stock_name, news_list):
    """
    Analyzes sentiment, importance, and adds keyword emphasis using LLM.
    """
    if not news_list:
        return []

    api_key = os.getenv("UPSTAGE_API_KEY")
    client = OpenAI(api_key=api_key, base_url="https://api.upstage.ai/v1/solar")

    news_context = ""
    for i, news in enumerate(news_list):
        news_context += f"[{i+1}] Source: {news['source']} | Title: {news['title']}\n"

    # [Language Setup]
    lang_code = os.getenv("REPORT_LANGUAGE", "en").lower()
    target_lang = "Korean" if lang_code == "ko" else "English"

    system_prompt = f"""
    You are a professional Stock News Analyst for '{stock_name}'.
    Analyze the provided news headlines.

    Tasks:
    1. Sentiment: Tag as '🟢 Positive', '🔴 Negative', or '⚪ Neutral'.
    2. Importance: Score from 1 (Trivial) to 5 (Critical Market Mover).
    3. Keywords: Identify 1-2 key words in the title and wrap them with markdown bold (**word**).
    4. Language: Output the results in {target_lang}.

    Output format must be a JSON list of objects:
    [
        {{
            "sentiment": "🟢 Positive",
            "importance": 4,
            "processed_title": "Tesla **Earnings** beat expectations...",
            "translated_title": "..." 
        }}
    ]
    """

    try:
        response = client.chat.completions.create(
            model="solar-1-mini-chat",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": news_context}
            ],
            temperature=0.1
        )
        
        content = response.choices[0].message.content
        cleaned = content.replace("```json", "").replace("```", "").strip()
        analysis_result = json.loads(cleaned)
        
        for i, item in enumerate(news_list):
            if i < len(analysis_result):
                ai_data = analysis_result[i]
                item["sentiment"] = ai_data.get("sentiment", "⚪ Neutral")
                item["importance"] = ai_data.get("importance", 1)
                
                # Apply processed title and ensure translation is set
                translated_title = ai_data.get("translated_title")
                processed_title = ai_data.get("processed_title")
                
                if translated_title and translated_title != item["title"]:
                    item["title"] = translated_title
                else:
                    item["title"] = processed_title or item["title"]
            else:
                item["sentiment"] = "⚪ Neutral"
                
        return news_list

    except Exception as e:
        print(f"AI Analysis Error: {e}")
        return news_list

def get_interested_stock_news():
    """
    Main execution function for collecting and analyzing news.
    """
    print("📰 Starting stock news collection and AI analysis...")
    results = []

    for stock in TARGET_STOCKS:
        ticker = stock["ticker"]
        name = stock["name"]
        lang = stock.get("lang", "en")
        limit = stock.get("limit", 2)

        print(f"   -> Collecting news for {name} ({ticker})...")
        
        # 1. Collect news
        raw_news = get_google_news_rss(name, lang, limit)
        
        # 2. AI Analysis
        if raw_news:
            analyzed_news = analyze_news_sentiment(name, raw_news)
            for item in analyzed_news:
                item["ticker"] = ticker 
                results.append(item)
    
    return results