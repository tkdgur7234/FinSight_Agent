# backend/services/sentiment_analysis.py

import feedparser
import cloudscraper # [Added] For bypassing Reddit's bot protection
import requests
import os
import json
import re
import time  # For time.sleep()
from datetime import datetime
from time import mktime
from bs4 import BeautifulSoup
from openai import OpenAI
from dotenv import load_dotenv
import random
load_dotenv()

# =========================================================
# ▼▼▼ [Configuration] Target Stocks List ▼▼▼
# =========================================================
# Note: 'avg_velocity' is just an initial value. Once data accumulates, it is dynamically calculated.
TARGET_STOCKS = [
    {
        "ticker": "SNDK",         
        "name": "SanDisk Corp",
        "fetch_limit": 100,
        "avg_velocity": 10, # Initial value (used when no historical data exists)
        "use_naver": False 
    },
    {
        "ticker": "TSLA",         
        "name": "Tesla",
        "fetch_limit": 100,
        "avg_velocity": 10, # Initial value
        "use_naver": False 
    },
    #{
    #   "ticker": "005930",       
    #    "name": "Samsung Electronics",
    #    "fetch_limit": 50,
    #    "avg_velocity": 20,
    #    "use_naver": True 
    #}
]

MODEL_FAST = "solar-1-mini-chat"
MODEL_SMART = "solar-pro2"
HISTORY_FILE = "velocity_history.json"  # File to store velocity history

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
# [New Feature] File-based Velocity Data Management
# ---------------------------------------------------------
def load_velocity_history():
    """Loads recorded velocity data."""
    if not os.path.exists(HISTORY_FILE):
        return {}
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_velocity_history(history):
    """Saves velocity data to a file."""
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ History Save Error: {e}")

def get_dynamic_avg_velocity(ticker, default_val):
    """
    [Core] Calculates the 'dynamic average velocity' based on recorded history.
    Uses the average of the last 10 records.
    """
    history = load_velocity_history()
    records = history.get(ticker, [])
    
    if not records:
        return default_val # Use default value if no records exist
    
    # Extract only the 'velocity' values from a list of dictionaries
    velocities = []
    for r in records:
        if isinstance(r, dict) and 'velocity' in r:
            velocities.append(r['velocity'])
        elif isinstance(r, (int, float)): # Compatibility for old numeric data
            velocities.append(r)
            
    if not velocities:
        return default_val

    # Use average of recent 14 days
    recent_velocities = velocities[-14:]
    avg = sum(recent_velocities) / len(recent_velocities)
    
    return avg

def update_velocity_history(ticker, current_velocity):
    """
    Updates today's record (overwrite) if it exists,
    otherwise adds (append) a new record.
    """
    if current_velocity <= 0: return

    history = load_velocity_history()
    if ticker not in history:
        history[ticker] = []
    
    today_str = datetime.now().strftime("%Y-%m-%d")
    records = history[ticker]
    
    # Check if the last record is today's
    is_today_exist = False
    
    if records:
        last_record = records[-1]
        if isinstance(last_record, dict) and last_record.get('date') == today_str:
            # Overwrite today's record with the latest value
            last_record['velocity'] = current_velocity
            is_today_exist = True
            
    # Add new if today's record doesn't exist
    if not is_today_exist:
        records.append({
            "date": today_str,
            "velocity": current_velocity
        })
    
    # Maintain only the last 60 days of data
    if len(records) > 60:
        history[ticker] = records[-60:]
        
    save_velocity_history(history)

def check_volume_spike(ticker, posts, default_velocity):
    if len(posts) < 5: return "Not Enough Data", 0
    try:
        newest_date = posts[0]['dt']
        oldest_date = posts[-1]['dt']
        diff_seconds = (newest_date - oldest_date).total_seconds()
        diff_hours = diff_seconds / 3600
        if diff_hours <= 0: diff_hours = 0.01
        
        # 1. Calculate current velocity
        current_velocity = len(posts) / diff_hours
        
        # 2. Get dynamic average velocity (DB replacement)
        avg_velocity = get_dynamic_avg_velocity(ticker, default_velocity)
        
        # 3. Save this measurement to history
        update_velocity_history(ticker, current_velocity)

        ratio = current_velocity / avg_velocity if avg_velocity > 0 else 1.0
        
        status = "Normal"
        if ratio > 2.5: status = "🔥 Volume Spike"
        elif ratio > 1.5: status = "⚠️ Active"
        
        # Debugging log
        print(f"   -> ⏱️ Velocity: {current_velocity:.1f} (Avg: {avg_velocity:.1f}) | Ratio: {ratio:.1f}x")
        
        return status, round(current_velocity, 1)
    except Exception as e:
        print(f"Calc Error: {e}")
        return "Calc Error", 0 

# [Updated] Use cloudscraper instead of requests.Session() to bypass Reddit's bot protection
reddit_session = cloudscraper.create_scraper()

def get_reddit_posts(ticker, limit):
    rss_url = f"https://www.reddit.com/r/stocks+wallstreetbets+investing+technology/search.rss?q={ticker}&sort=new&restrict_sr=on&limit=100"
    
    posts = []
    print(f"🔍 [Reddit] Attempting to collect {ticker} (Max 100)...")
    
    max_retries = 3
    for attempt in range(max_retries):
        # [Updated] Enhanced headers to mimic a real Chrome browser perfectly
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        }
        
        try:
            # Use cloudscraper (reddit_session) to send the request
            resp = reddit_session.get(rss_url, headers=headers, timeout=15)
            
            if resp.status_code == 200:
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
                
                return posts

            elif resp.status_code == 429:
                wait_time = (attempt + 1) * 5  
                print(f"   ⚠️ [Reddit] 429 Blocked! Retrying in {wait_time}s... ({attempt+1}/{max_retries})")
                time.sleep(wait_time)
                continue
            
            else:
                print(f"   ⚠️ [Reddit] Request failed for {ticker}! Status Code: {resp.status_code}")
                return []
                
        except Exception as e:
            print(f"   ⚠️ [Reddit] Network Error: {e}")
            time.sleep(2)
            
    print(f"   ❌ [Reddit] Gave up on {ticker} after max retries.")
    return []

def get_naver_posts(code, limit):
    posts = []
    if not code.isdigit(): return []

    print(f"🔍 [Naver HTML] Attempting to collect PC discussion board for {code}...")
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

def parse_json_safely(text):
    """
    Powerful JSON recovery logic: fixes broken brackets, handles newlines, 
    and corrects missing commas.
    """
    try:
        text = text.strip()
        # 1. Remove markdown code blocks
        text = re.sub(r'```json|```', '', text).strip()
        
        # 2. Force JSON structure correction (insert closing brackets if missing)
        if text.count('[') > text.count(']'):
            text += ']'
        if text.count('{') > text.count('}'):
            text += '}'
            
        # 3. Correct missing commas between list items caused by newlines
        text = re.sub(r'\"[\s\n]+\"', '","', text)
        
        # 4. Force fix if JSON doesn't start with an array or object
        start_idx = text.find('[')
        end_idx = text.rfind(']') + 1
        if start_idx == -1: # Try object if no array found
            start_idx = text.find('{')
            end_idx = text.rfind('}') + 1
            
        if start_idx != -1 and end_idx != -1:
            text = text[start_idx:end_idx]
            
        return json.loads(text)
    except Exception as e:
        print(f"   ❌ [Critical] Failed to recover JSON parsing: {e}")
        return []

def summarize_with_llm(ticker, posts):
    api_key = os.getenv("UPSTAGE_API_KEY")
    client = OpenAI(api_key=api_key, base_url="https://api.upstage.ai/v1/solar")

    # [Core] Limit to 50 posts to avoid token confusion
    recent_posts = posts[:50]
    full_content = "\n".join([f"- {p['text']}" for p in recent_posts])

    # [Language Setup]
    lang_code = os.getenv("REPORT_LANGUAGE", "en").lower()
    target_lang = "Korean" if lang_code == "ko" else "English"
    
    # [Prompt] Added clear formatting constraints
    system_prompt = f"""
    Analyze comments about {ticker}.
    Return exactly 10 key points.
    
    CRITICAL FORMATTING RULES:
    1. Output ONLY a valid JSON array of strings: ["point1", "point2"].
    2. NO markdown, NO explanations, NO extra text.
    3. Each string MUST be under 40 characters in {target_lang}.
    4. NO double quotes inside the text.
    """
    
    try:
        response = client.chat.completions.create(
            model=MODEL_FAST,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": full_content}],
            temperature=0.0, 
            timeout=30,
            max_tokens=1000 # 1000 tokens are sufficient for this task
        )
        
        return parse_json_safely(response.choices[0].message.content)
    except Exception as e:
        print(f"   ❌ LLM API Error ({ticker}): {e}")
        return []

def analyze_final_sentiment(ticker, key_sentences):
    api_key = os.getenv("UPSTAGE_API_KEY")
    client = OpenAI(api_key=api_key, base_url="https://api.upstage.ai/v1/solar")

    # [Dynamic Language Setup]
    lang_code = os.getenv("REPORT_LANGUAGE", "en").lower()
    target_lang = "Korean" if lang_code == "ko" else "English"

    sentences_text = "\n".join([f"{i+1}. {s}" for i, s in enumerate(key_sentences)])
    
    system_prompt = f"""
    Analyze investor sentiment for {ticker}.
    Output JSON: {{ "score": <0-100>, "status": "<Extreme Fear/Fear/Neutral/Greed/Extreme Greed>", "reason_korean": "<Write the reason in {target_lang}>" }}
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
    print("🚀 Starting Community Sentiment Analysis...")
    
    for stock in TARGET_STOCKS:
        try:
            ticker = stock["ticker"]
            limit = stock["fetch_limit"]
            
            if ticker.isdigit():
                raw_posts = get_naver_posts(ticker, limit)
            else:
                raw_posts = get_reddit_posts(ticker, limit)
                
            if not raw_posts: 
                print(f"⚠️ [{stock['name']}] No data found (0 records).")
                continue
            
            vol_status, velocity = check_volume_spike(stock["name"], raw_posts, stock["avg_velocity"])
            filtered_count = len(raw_posts)
            
            print(f"🤖 [{stock['name']}] Summarizing ({filtered_count} records)...")
            key_sentences = summarize_with_llm(stock["name"], raw_posts)
            if not key_sentences: continue
            
            print(f"🧠 [{stock['name']}] Performing deep analysis...")
            final_data = analyze_final_sentiment(stock["name"], key_sentences)
            
            if final_data:
                final_data["ticker"] = stock["name"]
                final_data["volume_status"] = vol_status
                final_data["velocity"] = velocity
                final_data["filtered_count"] = filtered_count
                final_data["summary_sentences"] = key_sentences
                results.append(final_data)
                print(f"   -> ✅ Complete: {stock['name']}")
                
        except Exception as e:
            print(f"❌ [{stock.get('name')}] Error: {e}")
            continue

        # [Anti-Bot Strategy] Human-like behavior: Wait 8 to 20 seconds between requests
        delay = random.uniform(8.0, 20.0)
        print(f"   ⏳ [Anti-Bot] Waiting {delay:.1f}s before next stock...")
        time.sleep(delay)
    return results