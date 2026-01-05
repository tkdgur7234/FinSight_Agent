# backend/services/market_new_crawl.py

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

# --- [전략 수정] Positive Filter 위주의 정밀 쿼리 ---
# 2. Positive Filter 강화: 지수명 + 마감키워드(Close/Ends) 필수 포함(AND)
# 3. 시간 단축: when:12h (최근 12시간)으로 설정하여 '어제 아침' 뉴스 배제

TRACKS = [
    {
        # [Track A] 장 마감 시황 (Market Wrap)
        # S&P 500 또는 Nasdaq이 제목에 꼭 있어야 하고, 'Close'나 'Wrap' 같은 마감 단어가 필수
        "name": "Track A: Market Wrap (현상)",
        "url": 'https://news.google.com/rss/search?q=("S%26P+500"+OR+"Nasdaq")+AND+("close"+OR+"ends"+OR+"settles"+OR+"wrap")+when:12h&hl=en-US&gl=US&ceid=US:en',
        "limit": 2
    },
    {
        # [Track B] 등락 원인 (Why it moved)
        # "Stocks"나 "Wall Street"가 주어이고, 인과관계(due to, as)를 설명하는 기사
        "name": "Track B: Why it moved (원인)",
        "url": 'https://news.google.com/rss/search?q=("US+stocks"+OR+"Wall+Street")+AND+("rise"+OR+"fall"+OR+"climb"+OR+"drop")+AND+("due+to"+OR+"as"+OR+"on")+when:12h&hl=en-US&gl=US&ceid=US:en',
        "limit": 4
    },
    {
        # [Track C] 주도주 (Movers)
        # 'Active stocks' 등으로 검색하되, Track A/B에서 다룬 내용과 겹치지 않게 개별 종목 위주
        "name": "Track C: Active Movers (주도주)",
        "url": 'https://news.google.com/rss/search?q=("S%26P+500"+OR+"Nasdaq")+AND+("biggest+movers"+OR+"active+stocks")+when:12h&hl=en-US&gl=US&ceid=US:en',
        "limit": 2
    }
]

def clean_html(raw_html):
    """HTML 태그 제거"""
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    return unescape(cleantext).strip()

def convert_pubdate_to_kst(pub_date_str):
    """RSS 날짜(GMT) -> KST 변환"""
    try:
        dt_obj = datetime.strptime(pub_date_str, "%a, %d %b %Y %H:%M:%S %Z")
        dt_utc = dt_obj.replace(tzinfo=pytz.utc)
        kst_tz = pytz.timezone('Asia/Seoul')
        return dt_utc.astimezone(kst_tz).strftime("%Y-%m-%d %H:%M:%S KST")
    except Exception:
        return pub_date_str

def get_market_news():
    """
    3-Track 전략 수집 (Positive Filter 적용)
    """
    all_articles = []
    seen_links = set()

    print("🚀 3-Track 미국 증시 뉴스 크롤링 (Positive Filter)...")

    try:
        for track in TRACKS:
            feed = feedparser.parse(track["url"])
            count = 0
            
            for entry in feed.entries:
                if count >= track["limit"]:
                    break
                
                # 중복 URL 체크
                if entry.link in seen_links:
                    continue
                seen_links.add(entry.link)
                
                # 날짜 변환
                pub_date = entry.published if 'published' in entry else ""
                kst_date = convert_pubdate_to_kst(pub_date)

                # Description 전처리
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
            
            print(f"✅ {track['name']} - {count}개 수집 완료")

        if not all_articles:
            return {"status": "error", "message": "No news found"}

        # AI 분석 요청
        ai_result = analyze_with_upstage_summary(all_articles)
        
        return {
            "status": "success",
            "market_summary": ai_result.get("market_summary", "요약 생성 실패"),
            "news_list": ai_result.get("news_list", all_articles)
        }

    except Exception as e:
        print(f"News Crawl Error: {e}")
        return {"status": "error", "message": str(e)}

def analyze_with_upstage_summary(articles):
    """
    Upstage Solar API: 종합 요약 + 번역
    """
    api_key = os.getenv("UPSTAGE_API_KEY")
    if not api_key:
        print("⚠️ Upstage API Key missing")
        return {"market_summary": "API Key 없음", "news_list": articles}

    client = OpenAI(
        api_key=api_key,
        base_url="https://api.upstage.ai/v1/solar"
    )

    context_text = ""
    for i, a in enumerate(articles):
        context_text += f"[News {i+1}] ({a['track']}) - {a['pub_date']}\nTitle: {a['title']}\nContent: {a['summary_raw'][:300]}\n\n"

    # [프롬프트] 'Market Close' 시점을 명시적으로 강조
    system_prompt = """
    You are an expert AI Financial Analyst specializing in the US Stock Market. 
    Your goal is to write a 'Daily Market Briefing' for Korean investors.

    Task 1: Market Driver Synthesis
    - Focus on the 'Market Close' results from the provided news.
    - Identify the primary reason for the market's movement (e.g., S&P 500 rose due to tech earnings).
    - Write a cohesive paragraph (3-4 sentences) **in Korean**.

    Task 2: Headline Translation
    - Translate the titles into professional Korean business language.

    Output MUST be in JSON format:
    {
        "market_summary": "한국어 요약...",
        "news_list": [
            {"korean_title": "...", "original_title": "..."}
        ]
    }
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
            korean_title = article["title"]
            if i < len(ai_list):
                korean_title = ai_list[i].get("korean_title", article["title"])
            
            final_news_list.append({
                "title": korean_title,
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
        return {"market_summary": "AI 분석 중 오류 발생", "news_list": articles}