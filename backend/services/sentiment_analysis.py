import feedparser
import requests
import os
import json
import re
from datetime import datetime
from time import mktime
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# =========================================================
# ▼▼▼ [사용자 설정] 종목 리스트 ▼▼▼
# =========================================================
TARGET_STOCKS = [
    {
        "ticker": "TSLA",         # [미국] Reddit 검색어
        "name": "Tesla",
        "fetch_limit": 50,
        "avg_velocity": 10
    },
    {
        "ticker": "005930",       # [한국] 종목코드 (삼성전자)
        "name": "삼성전자",
        "fetch_limit": 50,
        "avg_velocity": 20
    }
]

# ▼▼▼ [모델 설정] Update: solar-pro -> solar-pro2 ▼▼▼
MODEL_FAST = "solar-1-mini-chat"   # 단순 요약용
MODEL_SMART = "solar-pro2"          # 고성능 분석용

SPAM_KEYWORDS = ["crypto", "whatsapp", "telegram", "giveaway", "free", "discord", "리딩", "무료", "카톡", "band"]

def clean_text(text):
    """특수문자 및 불필요한 공백 제거"""
    text = re.sub(r'<[^>]+>', '', text) # HTML 태그 제거
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def parse_json_safely(text):
    """
    [핵심 수정] LLM 응답에서 JSON 부분만 정규식으로 정밀 추출
    오류 원인: JSON 뒤에 잡담이 섞여 있으면 json.loads()가 터짐
    """
    try:
        # 1. ```json ... ``` 코드 블록 제거
        text = text.replace("```json", "").replace("```", "").strip()
        
        # 2. 가장 겉에 있는 {} 또는 [] 찾기
        # DOTALL: 줄바꿈이 있어도 매칭
        match = re.search(r'(\{.*\}|\[.*\])', text, re.DOTALL)
        if match:
            clean_json = match.group(1)
            return json.loads(clean_json)
        else:
            # 매칭 안되면 원본 시도
            return json.loads(text)
    except Exception:
        return None

def check_volume_spike(posts, avg_velocity):
    """
    게시글 리젠 속도 계산 (Naver/Reddit 통합 지원)
    """
    if len(posts) < 5: return "데이터 부족", 0

    try:
        # 최신 글과 가장 오래된 글의 시간 차이 계산
        newest_date = posts[0]['dt']
        oldest_date = posts[-1]['dt']
        
        # 시간 차이 (시간 단위)
        diff_seconds = (newest_date - oldest_date).total_seconds()
        diff_hours = diff_seconds / 3600
        
        if diff_hours <= 0: diff_hours = 0.01 # 0으로 나누기 방지

        velocity = len(posts) / diff_hours
        ratio = velocity / avg_velocity if avg_velocity > 0 else 1.0

        status = "Normal"
        if ratio > 2.5: status = "🔥 Volume Spike"
        elif ratio > 1.5: status = "⚠️ Active"
        
        return status, round(velocity, 1)

    except Exception as e:
        # print(f"Velocity Calc Error: {e}")
        return "Calc Error", 0

def get_reddit_posts(ticker, limit):
    """Reddit RSS 크롤링"""
    rss_url = f"https://www.reddit.com/r/stocks+wallstreetbets+investing+technology/search.rss?q={ticker}&sort=new&restrict_sr=on&limit={limit+20}"
    feed = feedparser.parse(rss_url)
    posts = []
    
    print(f"🔍 [Reddit] {ticker} 수집 중...")
    for entry in feed.entries:
        if len(posts) >= limit: break
        
        content = clean_text(entry.description) if 'description' in entry else ""
        full_text = f"{entry.title} {content}"
        
        if len(full_text) < 10: continue
        if any(k in full_text.lower() for k in SPAM_KEYWORDS): continue
        
        # 날짜 표준화 (struct_time -> datetime)
        dt = datetime.fromtimestamp(mktime(entry.published_parsed))
        
        posts.append({
            "text": full_text[:500],
            "dt": dt
        })
    return posts

def get_naver_posts(code, limit):
    """
    [업그레이드] 네이버 모바일 증권 API 사용 (JSON 파싱)
    HTML 파싱보다 빠르고 날짜 정보를 정확히 얻을 수 있음
    """
    posts = []
    print(f"🔍 [Naver API] {code} 종토방 수집 중...")
    
    # 네이버 모바일 종목토론실 API URL
    url = f"https://m.stock.naver.com/api/discuss/local/{code}?offset=0&limit={limit+10}"
    headers = {
        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 13_2_3 like Mac OS X)',
        'Referer': f'https://m.stock.naver.com/domestic/stock/{code}/discuss'
    }
    
    try:
        res = requests.get(url, headers=headers)
        data = res.json()
        
        # API 구조: data --> 리스트 형태
        for item in data:
            if len(posts) >= limit: break
            
            title = item.get('title', '')
            contents = item.get('contents', '')
            full_text = f"{title} {contents}"
            full_text = clean_text(full_text)
            
            # 날짜 파싱 (API는 '2025-01-05 14:30:00' 형태로 줌)
            date_str = item.get('date', '') # YYYY-MM-DD HH:MM:SS
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
            except:
                dt = datetime.now() # 에러 시 현재 시간

            if len(full_text) < 5: continue
            if any(k in full_text for k in SPAM_KEYWORDS): continue
            
            posts.append({
                "text": full_text[:300], # 너무 길면 자름
                "dt": dt
            })
            
    except Exception as e:
        print(f"Naver API Error: {e}")
        
    return posts

def summarize_with_llm(ticker, posts):
    """
    [2차 필터링 & 요약] -> solar-1-mini-chat
    """
    api_key = os.getenv("UPSTAGE_API_KEY")
    client = OpenAI(api_key=api_key, base_url="https://api.upstage.ai/v1/solar")

    # 최근 글 순서대로 텍스트 병합
    context_text = "\n".join([f"- {p['text']}" for p in posts])

    system_prompt = f"""
    You are a data filtering assistant.
    Filter out noise from the comments about {ticker}.
    Select exactly **10 most meaningful sentences** that explain the current investor sentiment.
    
    Output format:
    A pure JSON list of strings. 
    Example: ["High expectation for earnings...", "Worried about CEO risk..."]
    """

    try:
        response = client.chat.completions.create(
            model=MODEL_FAST,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context_text}
            ],
            temperature=0.1
        )
        content = response.choices[0].message.content
        
        # [수정] 안전한 JSON 파싱 함수 사용
        parsed_data = parse_json_safely(content)
        if isinstance(parsed_data, list):
            return parsed_data
        else:
            return []
            
    except Exception as e:
        print(f"Summary Error: {e}")
        return []

def analyze_final_sentiment(ticker, key_sentences):
    """
    [최종 분석] -> solar-pro2
    """
    api_key = os.getenv("UPSTAGE_API_KEY")
    client = OpenAI(api_key=api_key, base_url="https://api.upstage.ai/v1/solar")

    sentences_text = "\n".join([f"{i+1}. {s}" for i, s in enumerate(key_sentences)])

    system_prompt = f"""
    You are an expert Stock Sentiment Analyst.
    Based on the key user opinions for {ticker}, provide a deep analysis.

    Output JSON Format:
    {{
        "score": <int 0-100>,
        "status": "<Fear/Neutral/Greed>",
        "reason_korean": "<Explain the reason in Korean>"
    }}
    """

    try:
        response = client.chat.completions.create(
            model=MODEL_SMART, # solar-pro2 사용
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": sentences_text}
            ],
            temperature=0.1
        )
        content = response.choices[0].message.content
        
        # [수정] 안전한 JSON 파싱 함수 사용
        return parse_json_safely(content)
        
    except Exception as e:
        print(f"Analysis Error: {e}")
        return None

def get_sentiment_analysis():
    results = []
    print("🚀 커뮤니티 감성 분석 시작...")
    
    for stock in TARGET_STOCKS:
        ticker = stock["ticker"]
        limit = stock["fetch_limit"]
        
        # 1. 소스 분기 (숫자면 네이버, 아니면 Reddit)
        if ticker.isdigit():
            raw_posts = get_naver_posts(ticker, limit)
        else:
            raw_posts = get_reddit_posts(ticker, limit)
            
        if not raw_posts: 
            print(f"⚠️ {stock['name']} 데이터 없음")
            continue
        
        # 2. Volume Spike (이제 네이버도 가능!)
        vol_status, velocity = check_volume_spike(raw_posts, stock["avg_velocity"])
        
        # 3. 요약 (Mini)
        print(f"🤖 [{stock['name']}] 핵심 요약 추출 중 ({MODEL_FAST})...")
        key_sentences = summarize_with_llm(stock["name"], raw_posts)
        
        if not key_sentences: 
            print("   -> 요약 실패")
            continue
        
        # 4. 최종 분석 (Pro2)
        print(f"🧠 [{stock['name']}] 감성 분석 중 ({MODEL_SMART})...")
        final_data = analyze_final_sentiment(stock["name"], key_sentences)
        
        if final_data:
            final_data["ticker"] = stock["name"]
            final_data["volume_status"] = vol_status
            final_data["velocity"] = velocity
            final_data["summary_sentences"] = key_sentences
            results.append(final_data)
            print("   -> 분석 완료 ✅")
            
    return results