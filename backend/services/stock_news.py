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
# ▼▼▼ [설정] 관심 종목 뉴스 리스트 ▼▼▼
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
    },
    #{
    #    "ticker": "005930",
    #    "name": "삼성전자",
    #    "lang": "ko",
    #    "limit": 2
    #}
]

# =========================================================
# ▼▼▼ [설정] 유료(Paywall) 뉴스 소스 블랙리스트 ▼▼▼
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
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    return unescape(cleantext).strip()

def is_similar(a, b, threshold=0.5):
    """제목 유사도 검사"""
    return SequenceMatcher(None, a, b).ratio() > threshold

def is_paywalled(source_name):
    if not source_name: return False
    source_lower = source_name.lower()
    for blocked in PAYWALLED_SOURCES:
        if blocked.lower() in source_lower:
            return True
    return False

def get_google_news_rss(query, lang="en", limit=2):
    """
    구글 뉴스 RSS 크롤링 (상세 디버깅 로그 추가)
    """
    
    # 통계 집계용 변수
    stats = {
        "total_fetched": 0,
        "dropped_paywall": 0,
        "dropped_time": 0,
        "dropped_dup": 0,
        "accepted": 0
    }
    
    # 1. 넉넉하게 가져오기
    fetch_count = 15
    
    if lang == 'ko':
        rss_url = f"https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"
    else:
        rss_url = f"https://news.google.com/rss/search?q={query}+when:24h&hl=en-US&gl=US&ceid=US:en"

    try:
        feed = feedparser.parse(rss_url)
        news_results = []
        seen_titles = [] 
        
        # 시간 필터 설정
        kst_tz = pytz.timezone('Asia/Seoul')
        now_kst = datetime.now(kst_tz)
        cutoff_end = now_kst
        cutoff_start = now_kst - timedelta(hours=24) 

        # 전체 가져온 개수 기록
        stats["total_fetched"] = len(feed.entries)
        # print(f"   🔍 [Debug] '{query}' 원본 {stats['total_fetched']}개 발견")

        for entry in feed.entries:
            # 목표 개수 채우면 중단
            if len(news_results) >= limit:
                break
                
            if len(seen_titles) >= fetch_count * 2:
                break

            # ---------------------------------------------------------
            # [필터 1] 유료 매체(Paywall)
            # ---------------------------------------------------------
            source_name = entry.source.title if 'source' in entry else ""
            if is_paywalled(source_name):
                stats["dropped_paywall"] += 1
                # print(f"      🚫 [Skip:유료] {source_name}")
                continue

            # [필터 2] 날짜 정밀 필터링
            pub_date_str = entry.published if 'published' in entry else ""
            try:
                dt_obj = date_parser.parse(pub_date_str)
                if dt_obj.tzinfo is None:
                    dt_obj = dt_obj.replace(tzinfo=pytz.utc)
                article_dt_kst = dt_obj.astimezone(kst_tz)
                
                if not (cutoff_start <= article_dt_kst <= cutoff_end):
                    stats["dropped_time"] += 1
                    # print(f"      ⏰ [Skip:시간] {article_dt_kst.strftime('%m-%d %H:%M')} (범위 밖)")
                    continue 
            except Exception:
                continue 

            # [필터 3] 중복 제거
            title = entry.title
            is_dup = False
            for seen in seen_titles:
                if is_similar(title, seen):
                    is_dup = True
                    break
            
            if is_dup:
                stats["dropped_dup"] += 1
                # print(f"      👯 [Skip:중복] {title[:20]}...")
                continue 
            
            # -- 통과 --
            seen_titles.append(title)
            stats["accepted"] += 1
            
            pub_date_fmt = article_dt_kst.strftime("%Y-%m-%d %H:%M")
            
            news_results.append({
                "title": entry.title,
                "link": entry.link,
                "pub_date": pub_date_fmt,
                "source": source_name or "Google News"
            })
            
        # [최종 로그 출력] 왜 0개가 나왔는지 확인 가능
        if stats["accepted"] == 0:
            print(f"   ⚠️ [Result] '{query}' 수집 0건! (원인: 시간탈락 {stats['dropped_time']}건, 유료탈락 {stats['dropped_paywall']}건, 중복탈락 {stats['dropped_dup']}건)")
            
        return news_results

    except Exception as e:
        print(f"RSS Error ({query}): {e}")
        return []

def analyze_news_sentiment(stock_name, news_list):
    """
    AI를 이용한 태깅, 중요도 평가, 키워드 볼드 처리
    """
    if not news_list:
        return []

    api_key = os.getenv("UPSTAGE_API_KEY")
    client = OpenAI(api_key=api_key, base_url="https://api.upstage.ai/v1/solar")

    news_context = ""
    for i, news in enumerate(news_list):
        news_context += f"[{i+1}] Source: {news['source']} | Title: {news['title']}\n"

    system_prompt = f"""
    You are a professional Stock News Analyst for '{stock_name}'.
    Analyze the provided news headlines.

    Tasks:
    1. **Sentiment**: Tag as '🟢 호재' (Good), '🔴 악재' (Bad), or '⚪ 중립' (Neutral).
    2. **Importance**: Score from 1 (Trivial) to 5 (Critical Market Mover).
    3. **Keywords**: Identify 1-2 key words in the title and wrap them with markdown bold (**word**).
    4. **Translate**: If the title is in English, translate it to Korean naturally.

    Output format must be a JSON list of objects:
    [
        {{
            "sentiment": "🟢 호재",
            "importance": 4,
            "processed_title": "Tesla **Earnings** beat expectations...",
            "korean_title": "테슬라 **실적** 예상치 상회..." 
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
                item["sentiment"] = ai_data.get("sentiment", "⚪ 중립")
                item["importance"] = ai_data.get("importance", 1)
                
                if item.get("title") != ai_data.get("korean_title"):
                     item["display_title"] = ai_data.get("korean_title", item["title"])
                else:
                     item["display_title"] = ai_data.get("processed_title", item["title"])
            else:
                item["sentiment"] = "⚪ 중립"
                item["display_title"] = item["title"]
                
        return news_list

    except Exception as e:
        print(f"AI Analysis Error: {e}")
        return news_list

def get_interested_stock_news():
    """
    메인 실행 함수
    """
    print("📰 관심 종목 뉴스 수집 및 AI 분석 시작...")
    results = []

    for stock in TARGET_STOCKS:
        ticker = stock["ticker"]
        name = stock["name"]
        lang = stock.get("lang", "en")
        limit = stock.get("limit", 2)

        print(f"   -> {name} ({ticker}) 뉴스 수집 중...")
        
        # 1. 뉴스 수집
        raw_news = get_google_news_rss(name, lang, limit)
        
        # 2. AI 분석
        if raw_news:
            analyzed_news = analyze_news_sentiment(name, raw_news)
            results.append({
                "ticker": ticker,
                "name": name,
                "news": analyzed_news
            })
        else:
             results.append({
                "ticker": ticker,
                "name": name,
                "news": [] 
            })
    
    return results