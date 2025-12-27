# backend/services/market_new_crawl.py

import feedparser
import os
from openai import OpenAI
from dotenv import load_dotenv
import json
import re
from html import unescape

load_dotenv()

# --- [전략] 3-Track 미국 증시 중심 RSS ---
TRACKS = [
    {
        # [Track A] Market Wrap (현상): 장 마감 시황
        "name": "Track A: Market Wrap (현상)",
        "url": 'https://news.google.com/rss/search?q=("Wall+Street"+OR+"S%26P+500"+OR+"Nasdaq")+AND+("close"+OR+"wrap")+when:1d&hl=en-US&gl=US&ceid=US:en',
        "limit": 2
    },
    {
        # [Track B] Why it moved (원인): 인과관계 분석
        # "US Stocks" 등의 키워드로 맥락을 미국 증시로 한정 (아시아 뉴스라도 미국 증시와 연관되면 수집됨)
        "name": "Track B: Why it moved (원인)",
        "url": 'https://news.google.com/rss/search?q=("Wall+Street"+OR+"US+stocks")+AND+("rise"+OR+"fall")+AND+("due+to"+OR+"because"+OR+"on")+when:1d&hl=en-US&gl=US&ceid=US:en',
        "limit": 4
    },
    {
        # [Track C] Active Movers (주도주): 종목 중심
        "name": "Track C: Active Movers (주도주)",
        "url": 'https://news.google.com/rss/search?q="stock+market"+AND+("biggest+movers"+OR+"active+stocks")+when:1d&hl=en-US&gl=US&ceid=US:en',
        "limit": 2
    }
]

def clean_html(raw_html):
    """RSS Description의 HTML 태그 제거"""
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    return unescape(cleantext).strip()

def get_market_news():
    """
    3-Track 전략 수집 + 중복 제거 + AI 분석 (금지어 필터 제거됨)
    """
    all_articles = []
    seen_links = set()

    print("🚀 3-Track 미국 증시 뉴스 크롤링 시작...")

    try:
        for track in TRACKS:
            feed = feedparser.parse(track["url"])
            count = 0
            
            for entry in feed.entries:
                if count >= track["limit"]:
                    break
                
                # 1. 중복 URL 체크
                if entry.link in seen_links:
                    continue
                
                seen_links.add(entry.link)
                
                # Description 전처리
                raw_desc = entry.description if 'description' in entry else ""
                clean_desc = clean_html(raw_desc)
                summary_text = clean_desc if len(clean_desc) > 20 else entry.title

                all_articles.append({
                    "track": track["name"],
                    "title": entry.title,
                    "link": entry.link,
                    "pub_date": entry.published if 'published' in entry else "",
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
    Upstage Solar API: 종합 요약(한국어) + 제목 번역
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
        context_text += f"[News {i+1}] ({a['track']})\nTitle: {a['title']}\nContent: {a['summary_raw'][:300]}\n\n"

    # [프롬프트] 글로벌 이슈가 포함되더라도 미국 증시에 미친 영향을 중심으로 분석하도록 유도
    system_prompt = """
    You are an expert AI Financial Analyst specializing in the US Stock Market. 
    Your goal is to write a 'Daily Market Briefing'.

    Task 1: Market Driver Synthesis
    - Identify the single most critical reason why the US market moved yesterday.
    - If the cause is global (e.g., Japan rates, China stimulus, Geopolitics), explicitly explain how it affected the US market.
    - Write a cohesive paragraph (3-4 sentences) **in Korean language**.
    - **CRITICAL:** The 'market_summary' MUST be written in **Korean (Hangul)**.

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
            model="solar-1-mini-chat",
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
                "track": article["track"]
            })

        return {
            "market_summary": ai_data.get("market_summary", "-"),
            "news_list": final_news_list
        }

    except Exception as e:
        print(f"Upstage AI Logic Error: {e}")
        return {"market_summary": "AI 분석 중 오류 발생", "news_list": articles}