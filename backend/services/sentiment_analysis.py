import feedparser
import requests
import os
import json
import re
from datetime import datetime
from time import mktime
from bs4 import BeautifulSoup
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# =========================================================
# ▼▼▼ [설정] 종목 리스트 ▼▼▼
# =========================================================
# 이제 'avg_velocity'는 초기값일 뿐, 데이터가 쌓이면 무시됩니다.
TARGET_STOCKS = [
    {
        "ticker": "TSLA",         
        "name": "Tesla",
        "fetch_limit": 100,
        "avg_velocity": 10, # 초기값 (데이터 없을 때 사용)
        "use_naver": False 
    },
    {
        "ticker": "RKLB",         
        "name": "Rocket Lab",
        "fetch_limit": 100,
        "avg_velocity": 10, # 초기값 (데이터 없을 때 사용)
        "use_naver": False 
    },
    #{
    #   "ticker": "005930",       
    #    "name": "삼성전자",
    #    "fetch_limit": 50,
    #    "avg_velocity": 20, # 초기값
    #    "use_naver": True 
    #}
]

MODEL_FAST = "solar-1-mini-chat"
MODEL_SMART = "solar-pro2"
HISTORY_FILE = "velocity_history.json"  # 속도 기록 저장 파일

SPAM_KEYWORDS = ["whatsapp", "telegram", "giveaway", "free", "discord", "리딩", "무료", "카톡", "밴드", "가입", "고수익", "입장"]

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

# ---------------------------------------------------------
# [신규 기능] 파일 기반 속도 데이터 관리
# ---------------------------------------------------------
def load_velocity_history():
    """기록된 속도 데이터를 불러옵니다."""
    if not os.path.exists(HISTORY_FILE):
        return {}
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_velocity_history(history):
    """속도 데이터를 파일에 저장합니다."""
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ History Save Error: {e}")

def get_dynamic_avg_velocity(ticker, default_val):
    """
    [핵심] 저장된 기록을 바탕으로 '동적 평균 속도'를 계산합니다.
    최근 10번의 기록 평균을 사용합니다.
    """
    history = load_velocity_history()
    records = history.get(ticker, [])
    
    if not records:
        return default_val # 기록 없으면 설정값 사용
    
    # [변경] 딕셔너리 리스트에서 'velocity' 값만 추출
    # 예: [{'date': '...', 'velocity': 10}, ...] -> [10, 15, ...]
    velocities = []
    for r in records:
        if isinstance(r, dict) and 'velocity' in r:
            velocities.append(r['velocity'])
        elif isinstance(r, (int, float)): # 호환성: 옛날 숫자 데이터가 있다면 포함
            velocities.append(r)
            
    if not velocities:
        return default_val

    # 최근 14일(2주) 치 평균 사용
    recent_velocities = velocities[-14:]
    avg = sum(recent_velocities) / len(recent_velocities)
    
    return avg

def update_velocity_history(ticker, current_velocity):
    """
    오늘 날짜의 기록이 이미 있으면 '갱신(덮어쓰기)'하고,
    없으면 '추가(Append)'합니다.
    """
    if current_velocity <= 0: return

    history = load_velocity_history()
    if ticker not in history:
        history[ticker] = []
    
    today_str = datetime.now().strftime("%Y-%m-%d")
    records = history[ticker]
    
    # [핵심 로직] 마지막 기록이 오늘인지 확인
    is_today_exist = False
    
    if records:
        last_record = records[-1]
        # 기록이 딕셔너리 형태이고, 날짜가 오늘이면
        if isinstance(last_record, dict) and last_record.get('date') == today_str:
            # 오늘의 기록을 최신 값으로 업데이트 (덮어쓰기)
            last_record['velocity'] = current_velocity
            is_today_exist = True
            
    # 오늘 기록이 없으면 새로 추가
    if not is_today_exist:
        records.append({
            "date": today_str,
            "velocity": current_velocity
        })
    
    # 최근 60일 데이터만 유지
    if len(records) > 60:
        history[ticker] = records[-60:]
        
    save_velocity_history(history)

def check_volume_spike(ticker, posts, default_velocity):
    if len(posts) < 5: return "데이터 부족", 0
    try:
        newest_date = posts[0]['dt']
        oldest_date = posts[-1]['dt']
        diff_seconds = (newest_date - oldest_date).total_seconds()
        diff_hours = diff_seconds / 3600
        if diff_hours <= 0: diff_hours = 0.01
        
        # 1. 현재 속도 계산
        current_velocity = len(posts) / diff_hours
        
        # 2. [변경] 동적 평균 속도 가져오기 (DB 대용)
        # 기록된 평균을 우선 사용하고, 없으면 default_velocity 사용
        avg_velocity = get_dynamic_avg_velocity(ticker, default_velocity)
        
        # 3. 이번 측정값을 기록에 저장 (다음번 평균을 위해)
        # 단, '데이터 부족'이거나 이상치일 때는 저장 안 할 수도 있음
        update_velocity_history(ticker, current_velocity)

        ratio = current_velocity / avg_velocity if avg_velocity > 0 else 1.0
        
        status = "Normal"
        if ratio > 2.5: status = "🔥 Volume Spike"
        elif ratio > 1.5: status = "⚠️ Active"
        
        # 디버깅용 로그
        print(f"   -> ⏱️ 속도: {current_velocity:.1f} (평균: {avg_velocity:.1f}) | 비율: {ratio:.1f}배")
        
        return status, round(current_velocity, 1)
    except Exception as e:
        print(f"Calc Error: {e}")
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
            return []

        feed = feedparser.parse(resp.content)
        if not feed.entries:
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
    except:
        return []
    return posts

def get_naver_posts(code, limit):
    posts = []
    if not code.isdigit(): return []

    print(f"🔍 [Naver HTML] {code} PC 종토방 수집 시도...")
    headers = {'User-Agent': 'Mozilla/5.0'}
    page = 1
    
    while len(posts) < limit and page <= 5:
        try:
            url = f"https://finance.naver.com/item/board.naver?code={code}&page={page}"
            res = requests.get(url, headers=headers, timeout=5)
            if res.status_code != 200: break

            try:
                html_text = res.content.decode('utf-8')
            except:
                html_text = res.content.decode('cp949', 'ignore')
                
            soup = BeautifulSoup(html_text, 'html.parser')
            rows = soup.select("div.section.inner_sub table.type2 tbody tr")
            if not rows: break

            for row in rows:
                if len(posts) >= limit: break
                title_tag = row.select_one("td.title a")
                if not title_tag: continue
                
                title = title_tag.get("title", "").strip() or title_tag.text.strip()
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
        except:
            break
    return posts

def summarize_with_llm(ticker, posts):
    api_key = os.getenv("UPSTAGE_API_KEY")
    client = OpenAI(api_key=api_key, base_url="https://api.upstage.ai/v1/solar")

    full_content = "\n".join([f"- {p['text']}" for p in posts])
    if len(full_content) > 3000:
        full_content = full_content[:3000] + "...(truncated)"

    # [수정] 프롬프트 엄격화: 전체 번역 금지, 1줄 요약 강제
    system_prompt = f"""
    Analyze the comments about {ticker}.
    Select exactly 10 most meaningful points.
    
    CRITICAL RULES:
    1. Do NOT translate the whole post. SUMMARIZE each point into just ONE short Korean sentence.
    2. Output format MUST be a pure, valid JSON array of strings. Do not add any conversational text.
    
    Example format:
    ["테슬라 자율주행 기술에 대한 혼란이 가중되고 있습니다.", "단기 옵션 매도 시점에 대한 투자자들의 후회가 많습니다."]
    """
    try:
        response = client.chat.completions.create(
            model=MODEL_FAST,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": full_content}],
            temperature=0.1, 
            timeout=30,
            max_tokens=1500 # [핵심] 텍스트가 도중에 잘리지 않도록 여유 토큰 부여
        )
        
        raw_content = response.choices[0].message.content
        parsed_data = parse_json_safely(raw_content)
        
        # 디버깅: 실패 시 원본을 보여줌
        if not parsed_data:
            print(f"   ⚠️ [Debug] {ticker} 파싱 실패. LLM 원본 응답:\n{raw_content[:200]}...")
            return []
            
        return parsed_data
    except Exception as e:
        print(f"   ❌ LLM API Error ({ticker}): {e}")
        return []

def analyze_final_sentiment(ticker, key_sentences):
    api_key = os.getenv("UPSTAGE_API_KEY")
    client = OpenAI(api_key=api_key, base_url="https://api.upstage.ai/v1/solar")

    sentences_text = "\n".join([f"{i+1}. {s}" for i, s in enumerate(key_sentences)])
    system_prompt = f"""
    Analyze investor sentiment for {ticker}.
    Output JSON: {{ "score": <0-100>, "status": "<Extreme Fear/Fear/Neutral/Greed/Extreme Greed>", "reason_korean": "..." }}
    """
    try:
        response = client.chat.completions.create(
            model=MODEL_SMART,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": sentences_text}],
            temperature=0.1, timeout=30
        )
        return parse_json_safely(response.choices[0].message.content)
    except:
        return None

def get_sentiment_analysis():
    results = []
    print("🚀 커뮤니티 감성 분석 시작...")
    
    for stock in TARGET_STOCKS:
        try:
            ticker = stock["ticker"]
            limit = stock["fetch_limit"]
            
            if ticker.isdigit():
                raw_posts = get_naver_posts(ticker, limit)
            else:
                raw_posts = get_reddit_posts(ticker, limit)
                
            if not raw_posts: 
                print(f"⚠️ [{stock['name']}] 데이터 없음 (0건).")
                continue
            
            # [수정] check_volume_spike에 ticker를 전달하여 히스토리 관리
            vol_status, velocity = check_volume_spike(stock["name"], raw_posts, stock["avg_velocity"])
            filtered_count = len(raw_posts)
            
            print(f"🤖 [{stock['name']}] 요약 중 ({filtered_count}건)...")
            key_sentences = summarize_with_llm(stock["name"], raw_posts)
            if not key_sentences: continue
            
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
            print(f"❌ [{stock.get('name')}] 오류: {e}")
            continue
            
    return results