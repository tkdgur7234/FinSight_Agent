# backend/services/market_news_crawl.py

import feedparser
import os
from openai import OpenAI
from dotenv import load_dotenv
import json
import re
from html import unescape

load_dotenv()

# --- [전략] 3-Track RSS Query 설정 ---
TRACKS = [
    {
        "name": "Track A: Market Wrap (현상)",
        "url": 'https://news.google.com/rss/search?q="Stock+Market+Today"+OR+"Market+Wrap"+when:1d&hl=en-US&gl=US&ceid=US:en',
        "limit": 2
    },
    {
        "name": "Track B: Why it moved (원인)",
        "url": 'https://news.google.com/rss/search?q=("Wall+Street"+OR+"US+stocks")+AND+("rise"+OR+"fall")+AND+("due+to"+OR+"because"+OR+"on")+when:1d&hl=en-US&gl=US&ceid=US:en',
        "limit": 4
    },
    {
        "name": "Track C: Active Movers (주도주)",
        "url": 'https://news.google.com/rss/search?q="stock+market"+AND+("biggest+movers"+OR+"active+stocks")+when:1d&hl=en-US&gl=US&ceid=US:en',
        "limit": 2
    }
]

def clean_html(raw_html):
    """
    RSS Description에 포함된 HTML 태그 제거 및 엔티티 복원
    """
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    return unescape(cleantext).strip()

def get_market_news():
    """
    3-Track 전략으로 뉴스를 수집하고 중복 제거 후 AI 분석 수행
    """
    all_articles = []
    seen_links = set() # 중복 제거용 (URL 기준)

    print("🚀 3-Track 뉴스 크롤링 시작...")

    try:
        # 1. 트랙별 크롤링 수행
        for track in TRACKS:
            feed = feedparser.parse(track["url"])
            count = 0
            
            for entry in feed.entries:
                if count >= track["limit"]:
                    break
                
                # 중복 체크 (Link 기준)
                if entry.link in seen_links:
                    continue
                
                seen_links.add(entry.link)
                
                # Description 전처리 (토큰 절약 및 가독성)
                raw_desc = entry.description if 'description' in entry else ""
                clean_desc = clean_html(raw_desc)
                
                # 너무 짧거나 의미 없는 description은 제목으로 대체하거나 제외
                summary_text = clean_desc if len(clean_desc) > 20 else entry.title

                all_articles.append({
                    "track": track["name"],
                    "title": entry.title,
                    "link": entry.link,
                    "pub_date": entry.published if 'published' in entry else "",
                    "summary_raw": summary_text # AI에게 보낼 핵심 재료
                })
                count += 1
            
            print(f"✅ {track['name']} - {count}개 수집 완료")

        if not all_articles:
            return {"status": "error", "message": "No news found"}

        # 2. AI 분석 요청 (종합 요약)
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
    수집된 뉴스들의 Title + Description을 종합하여
    '시장 핵심 재료'를 한 문단으로 정리하고, 각 뉴스를 한국어로 번역
    """
    api_key = os.getenv("UPSTAGE_API_KEY")
    if not api_key:
        print("⚠️ Upstage API Key missing")
        return {"market_summary": "API Key 없음", "news_list": articles}

    client = OpenAI(
        api_key=api_key,
        base_url="https://api.upstage.ai/v1/solar"
    )

    # LLM에게 던질 텍스트 구성 (Title + Description)
    context_text = ""
    for i, a in enumerate(articles):
        context_text += f"[News {i+1}] ({a['track']})\nTitle: {a['title']}\nContent: {a['summary_raw'][:300]}\n\n"

    # [프롬프트 엔지니어링]
    system_prompt = """
    You are an expert AI Financial Analyst. 
    Your goal is to write a 'Daily Market Briefing' based on the provided US stock market news.

    Task 1: Market Driver Synthesis
    - Read all news headlines and contents.
    - Identify the single most critical reason why the market moved yesterday.
    - Write a cohesive paragraph (3-4 sentences) **in Korean language**.
    - **CRITICAL:** The 'market_summary' value MUST be written in **Korean (Hangul)**.

    Task 2: Headline Translation
    - Translate the titles of the provided news into professional Korean business language.

    Output MUST be in JSON format:
    {
        "market_summary": "여기에 한국어로 된 요약글을 적으세요...",
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
        
        # JSON 파싱 전처리
        cleaned_content = content.replace("```json", "").replace("```", "").strip()
        ai_data = json.loads(cleaned_content)
        
        # 원본 리스트에 한국어 제목 매핑
        final_news_list = []
        ai_list = ai_data.get("news_list", [])
        
        for i, article in enumerate(articles):
            korean_title = article["title"] # 기본값
            
            # AI 결과 순서 매칭 시도
            if i < len(ai_list):
                korean_title = ai_list[i].get("korean_title", article["title"])
            
            # 불필요한 필드 정리 후 최종 리스트 생성
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