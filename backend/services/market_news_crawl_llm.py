# backend/services/market_news_crawl_llm.py

import feedparser
import os
from openai import OpenAI
from dotenv import load_dotenv
import json
import re
from html import unescape
from datetime import datetime
import pytz

load_dotenv()

# --- [Strategy Update] Precision Queries based on Positive Filter ---
# 1. Enhanced Positive Filter: Must include Index name + closing keywords (Close/Ends) via AND
# 2. Reduced Timeframe: Set to when:12h (last 12 hours) to exclude 'yesterday morning' news

TRACKS = [
    {
        # [Track A] Market Wrap 
        # S&P 500 or Nasdaq must be in the title, along with a closing keyword like 'Close' or 'Wrap'
        "name": "Track A: Market Wrap",
        "url": 'https://news.google.com/rss/search?q=("S%26P+500"+OR+"Nasdaq")+AND+("close"+OR+"ends"+OR+"settles"+OR+"wrap")+when:12h&hl=en-US&gl=US&ceid=US:en',
        "limit": 2
    },
    {
        # [Track B] Why it moved
        # Subject is "Stocks" or "Wall Street", explaining causality (due to, as)
        "name": "Track B: Why it moved",
        "url": 'https://news.google.com/rss/search?q=("US+stocks"+OR+"Wall+Street")+AND+("rise"+OR+"fall"+OR+"climb"+OR+"drop")+AND+("due+to"+OR+"as"+OR+"on")+when:12h&hl=en-US&gl=US&ceid=US:en',
        "limit": 4
    },
    {
        # [Track C] Active Movers
        # Search via 'Active stocks', focusing on individual stocks to avoid overlap with Track A/B
        "name": "Track C: Active Movers",
        "url": 'https://news.google.com/rss/search?q=("S%26P+500"+OR+"Nasdaq")+AND+("biggest+movers"+OR+"active+stocks")+when:12h&hl=en-US&gl=US&ceid=US:en',
        "limit": 2
    }
]

def clean_html(raw_html):
    """Remove HTML tags"""
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    return unescape(cleantext).strip()

def convert_pubdate_to_kst(pub_date_str):
    """Convert RSS date (GMT) -> KST"""
    try:
        dt_obj = datetime.strptime(pub_date_str, "%a, %d %b %Y %H:%M:%S %Z")
        dt_utc = dt_obj.replace(tzinfo=pytz.utc)
        kst_tz = pytz.timezone('Asia/Seoul')
        return dt_utc.astimezone(kst_tz).strftime("%Y-%m-%d %H:%M:%S KST")
    except Exception:
        return pub_date_str

def get_market_news():
    """
    3-Track Strategy Collection (Positive Filter Applied)
    """
    all_articles = []
    seen_links = set()

    print("🚀 Crawling US Stock Market News via 3-Track Strategy...")

    try:
        for track in TRACKS:
            feed = feedparser.parse(track["url"])
            count = 0
            
            for entry in feed.entries:
                if count >= track["limit"]:
                    break
                
                # Check for duplicate URLs
                if entry.link in seen_links:
                    continue
                seen_links.add(entry.link)
                
                # Date conversion
                pub_date = entry.published if 'published' in entry else ""
                kst_date = convert_pubdate_to_kst(pub_date)

                # Description preprocessing
                raw_desc = entry.description if 'description' in entry else ""
                clean_desc = clean_html(raw_desc)
                summary_text = clean_desc if len(clean_desc) > 20 else entry.title

                all_articles.append({
                    "track": track["name"],
                    "title": entry.title,
                    "link": entry.link,
                    "pub_date": kst_date,
                    "summary_raw": summary_text
                })
                count += 1
            
            print(f"✅ {track['name']} - {count} articles collected")

        if not all_articles:
            return {"status": "error", "message": "No news found"}

        # Request AI Analysis
        ai_result = analyze_with_upstage_summary(all_articles)
        
        return {
            "status": "success",
            "market_summary": ai_result.get("market_summary", "Summary generation failed"),
            "news_list": ai_result.get("news_list", all_articles)
        }

    except Exception as e:
        print(f"News Crawl Error: {e}")
        return {"status": "error", "message": str(e)}

def analyze_with_upstage_summary(articles):
    """
    Upstage Solar API: Comprehensive Summary + Translation (Dynamic Language)
    """
    api_key = os.getenv("UPSTAGE_API_KEY")
    if not api_key:
        print("⚠️ Upstage API Key missing")
        return {"market_summary": "API Key Missing", "news_list": articles}

    # [Dynamic Language Setup] Read REPORT_LANGUAGE from .env
    lang_code = os.getenv("REPORT_LANGUAGE", "en").lower()
    target_lang = "Korean" if lang_code == "ko" else "English"

    client = OpenAI(
        api_key=api_key,
        base_url="https://api.upstage.ai/v1/solar"
    )

    context_text = ""
    for i, a in enumerate(articles):
        context_text += f"[News {i+1}] ({a['track']}) - {a['pub_date']}\nTitle: {a['title']}\nContent: {a['summary_raw'][:300]}\n\n"

    # [Prompt] Explicitly emphasize 'Market Close' and apply dynamic language
    system_prompt = f"""
    You are an expert AI Financial Analyst specializing in the US Stock Market. 
    Your goal is to write a 'Daily Market Briefing' for investors.

    Task 1: Market Driver Synthesis
    - Focus on the 'Market Close' results from the provided news.
    - Identify the primary reason for the market's movement (e.g., S&P 500 rose due to tech earnings).
    - Write a cohesive paragraph (3-4 sentences) **in {target_lang}**.

    Task 2: Headline Translation/Formatting
    - Translate or refine the titles into professional {target_lang} business language.

    Output MUST be in JSON format:
    {{
        "market_summary": "{target_lang} summary goes here...",
        "news_list": [
            {{"target_title": "...", "original_title": "..."}}
        ]
    }}
    """

    try:
        response = client.chat.completions.create(
            model="solar-pro2",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Here is the collected news data:\n{context_text}"}
            ],
            temperature=0.1
        )
        
        content = response.choices[0].message.content
        cleaned_content = content.replace("```json", "").replace("```", "").strip()
        ai_data = json.loads(cleaned_content)
        
        final_news_list = []
        ai_list = ai_data.get("news_list", [])
        
        for i, article in enumerate(articles):
            processed_title = article["title"]
            if i < len(ai_list):
                processed_title = ai_list[i].get("target_title", article["title"])
            
            final_news_list.append({
                "title": processed_title,
                "original_title": article["title"],
                "link": article["link"],
                "track": article["track"],
                "pub_date": article["pub_date"]
            })

        return {
            "market_summary": ai_data.get("market_summary", "-"),
            "news_list": final_news_list
        }

    except Exception as e:
        print(f"Upstage AI Logic Error: {e}")
        return {"market_summary": "Error occurred during AI analysis", "news_list": articles}