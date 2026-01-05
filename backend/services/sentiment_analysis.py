import feedparser
import requests
import os
import json
import re
from datetime import datetime
from time import mktime
from bs4 import BeautifulSoup  # HTML 파싱을 위해 추가
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# =========================================================
# ▼▼▼ [설정] 종목 리스트 ▼▼▼
# =========================================================
TARGET_STOCKS = [
    {
        "ticker": "TSLA",         
        "name": "Tesla",
        "fetch_limit": 50,
        "avg_velocity": 10,
        "use_naver": False   # 해외주식 -> Reddit 권장
    },
    {
        "ticker": "005930",       
        "name": "삼성전자",
        "fetch_limit": 50,
        "avg_velocity": 20,
        "use_naver": True    # 국내주식 -> Naver HTML 크롤링
    },
    {
        "ticker": "GOOG.O",       
        "name": "알파벳(구글)",
        "fetch_limit": 30,    
        "avg_velocity": 5,
        "use_naver": False   # 해외주식은 네이버 HTML 게시판이 없으므로 False로 설정
    }
]

MODEL_FAST = "solar-1-mini-chat"
MODEL_SMART = "solar-pro2"

SPAM_KEYWORDS = ["crypto", "whatsapp", "telegram", "giveaway", "free", "discord", "리딩", "무료", "카톡", "band"]

def clean_text(text):
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def parse_json_safely(text):
    try:
        text = text.strip()
        text = text.replace("```json", "").replace("```", "")
        
        start_idx = -1
        end_idx = -1

        if '[' in text and ']' in text:
            start_idx = text.find('[')
            end_idx = text.rfind(']') + 1
        elif '{' in text and '}' in text:
            start_idx = text.find('{')
            end_idx = text.rfind('}') + 1
            
        if start_idx != -1 and end_idx != -1:
            clean_json = text[start_idx:end_idx]
            return json.loads(clean_json)
        
        return json.loads(text)
    except Exception as e:
        return None

def check_volume_spike(posts, avg_velocity):
    if len(posts) < 5: return "데이터 부족", 0
    try:
        newest_date = posts[0]['dt']
        oldest_date = posts[-1]['dt']
        diff_seconds = (newest_date - oldest_date).total_seconds()
        diff_hours = diff_seconds / 3600
        if diff_hours <= 0: diff_hours = 0.01
        velocity = len(posts) / diff_hours
        ratio = velocity / avg_velocity if avg_velocity > 0 else 1.0
        
        status = "Normal"
        if ratio > 2.5: status = "🔥 Volume Spike"
        elif ratio > 1.5: status = "⚠️ Active"
        return status, round(velocity, 1)
    except:
        return "Calc Error", 0

def get_reddit_posts(ticker, limit):
    rss_url = f"https://www.reddit.com/r/stocks+wallstreetbets+investing+technology/search.rss?q={ticker}&sort=new&restrict_sr=on&limit=100"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }

    posts = []
    print(f"🔍 [Reddit] {ticker} 수집 시도 (Max 100)...")
    
    try:
        resp = requests.get(rss_url, headers=headers, timeout=10)
        
        if resp.status_code != 200:
            print(f"   -> Reddit 요청 실패 (Code: {resp.status_code})")
            return []

        feed = feedparser.parse(resp.content)
        
        if not feed.entries:
            print("   -> Reddit 데이터 0건")
            return []

        for entry in feed.entries:
            if len(posts) >= limit: break
            
            content = clean_text(entry.description) if 'description' in entry else ""
            full_text = f"{entry.title} {content}"
            
            if len(full_text) < 10: continue
            if any(k in full_text.lower() for k in SPAM_KEYWORDS): continue
            
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                dt = datetime.fromtimestamp(mktime(entry.published_parsed))
            else:
                dt = datetime.now()
                
            posts.append({"text": full_text[:500], "dt": dt})
            
    except Exception as e:
        print(f"   -> Reddit Error: {e}")
        return []
        
    return posts

def get_naver_posts(code, limit):
    """
    [완전 변경] 네이버 금융 PC 버전 HTML 크롤링 (API 미사용)
    대상 URL: https://finance.naver.com/item/board.naver?code={code}
    """
    posts = []
    
    # 1. 해외 주식 체크 (숫자가 아니면 지원 불가)
    if not code.isdigit():
        print(f"⚠️ [Naver] 해외주식({code})은 PC HTML 게시판이 없어 크롤링 불가능합니다. (Reddit 사용 권장)")
        return []

    print(f"🔍 [Naver HTML] {code} PC 종토방 수집 시도...")
    
    # PC 브라우저 User-Agent
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }

    page = 1
    max_page = 5 # 너무 많이 긁지 않도록 제한
    
    while len(posts) < limit and page <= max_page:
        try:
            url = f"https://finance.naver.com/item/board.naver?code={code}&page={page}"
            res = requests.get(url, headers=headers, timeout=5)
            
            if res.status_code != 200:
                print(f"   -> 페이지 접속 실패: {res.status_code}")
                break

            # 인코딩 설정 (한글 깨짐 방지)
            res.encoding = 'cp949' 
            soup = BeautifulSoup(res.text, 'html.parser')
            
            # 테이블 행 가져오기
            rows = soup.select("div.section.inner_sub table.type2 tbody tr")
            
            if not rows:
                break

            for row in rows:
                if len(posts) >= limit: break
                
                # 마우스 오버 시 나오는 본문 미리보기 or 제목
                title_tag = row.select_one("td.title a")
                if not title_tag: continue
                
                title = title_tag.get("title", "").strip()
                if not title:
                    title = title_tag.text.strip()
                
                # 날짜 추출 (YYYY.MM.DD HH:mm)
                date_tag = row.select_one("td:nth-of-type(6) span")
                date_str = date_tag.text.strip() if date_tag else ""
                
                try:
                    dt = datetime.strptime(date_str, "%Y.%m.%d %H:%M")
                except:
                    dt = datetime.now()

                full_text = clean_text(title)
                
                if len(full_text) < 2: continue
                if any(k in full_text for k in SPAM_KEYWORDS): continue
                
                posts.append({"text": full_text[:300], "dt": dt})
            
            page += 1
            
        except Exception as e:
            print(f"   -> Naver HTML Crawl Error: {e}")
            break
            
    return posts

def summarize_with_llm(ticker, posts):
    api_key = os.getenv("UPSTAGE_API_KEY")
    client = OpenAI(api_key=api_key, base_url="https://api.upstage.ai/v1/solar")

    context_text = "\n".join([f"- {p['text']}" for p in posts])

    system_prompt = f"""
    Filter out noise from the comments about {ticker}.
    Select exactly **10 most meaningful sentences**.
    Output format must be a pure JSON list of strings: ["Msg 1", "Msg 2"]
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
        result = parse_json_safely(content)
        return result if isinstance(result, list) else []
    except Exception as e:
        print(f"Summary Error: {e}")
        return []

def analyze_final_sentiment(ticker, key_sentences):
    api_key = os.getenv("UPSTAGE_API_KEY")
    client = OpenAI(api_key=api_key, base_url="https://api.upstage.ai/v1/solar")

    sentences_text = "\n".join([f"{i+1}. {s}" for i, s in enumerate(key_sentences)])
    
    system_prompt = f"""
    Analyze investor sentiment for {ticker}.
    Output JSON:
    {{
        "score": <int 0-100>,
        "status": "<Fear/Neutral/Greed>",
        "reason_korean": "<Explain in Korean>"
    }}
    """

    try:
        response = client.chat.completions.create(
            model=MODEL_SMART, 
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": sentences_text}
            ],
            temperature=0.1
        )
        content = response.choices[0].message.content
        return parse_json_safely(content)
    except Exception as e:
        print(f"Analysis Error: {e}")
        return None

def get_sentiment_analysis():
    results = []
    print("🚀 커뮤니티 감성 분석 시작...")
    
    for stock in TARGET_STOCKS:
        try:
            ticker = stock["ticker"]
            limit = stock["fetch_limit"]
            
            # [수정] 사용자가 강제로 use_naver=True를 해도, 해외주식(문자 티커)은 HTML 크롤링 불가하므로 강제 Reddit 전환
            use_naver = stock.get("use_naver", False)
            
            if use_naver and not ticker.isdigit():
                print(f"⚠️ [{stock['name']}] 네이버 HTML 크롤링은 국내주식(숫자코드)만 지원합니다. Reddit으로 전환합니다.")
                use_naver = False

            if use_naver:
                raw_posts = get_naver_posts(ticker, limit)
            else:
                raw_posts = get_reddit_posts(ticker, limit)
                
            if not raw_posts: 
                print(f"⚠️ [{stock['name']}] 수집된 데이터가 없습니다 (0건).")
                continue
            
            # 2. 분석
            vol_status, velocity = check_volume_spike(raw_posts, stock["avg_velocity"])
            filtered_count = len(raw_posts)
            
            print(f"🤖 [{stock['name']}] 요약 중 ({filtered_count}건)...")
            key_sentences = summarize_with_llm(stock["name"], raw_posts)
            
            if not key_sentences: 
                print(f"   -> 요약 실패 (LLM 응답 오류)")
                continue
            
            print(f"🧠 [{stock['name']}] 심층 분석 중...")
            final_data = analyze_final_sentiment(stock["name"], key_sentences)
            
            if final_data:
                final_data["ticker"] = stock["name"]
                final_data["volume_status"] = vol_status
                final_data["velocity"] = velocity
                final_data["filtered_count"] = filtered_count
                final_data["summary_sentences"] = key_sentences
                results.append(final_data)
                print(f"   -> ✅ 완료: {stock['name']}")
                
        except Exception as e:
            print(f"❌ [{stock.get('name')}] 처리 중 치명적 오류: {e}")
            continue
            
    return results