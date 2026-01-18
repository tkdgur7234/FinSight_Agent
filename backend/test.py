# test_fmp_raw.py
import os
import requests
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()

api_key = os.getenv("FMP_API_KEY")
print(f"🔑 [설정 확인] API Key: {api_key}")

# 1. 시세 데이터 (Quote) - 가장 기본적이고 무료인 API
ticker = "TSLA"
url_quote = f"https://financialmodelingprep.com/api/v3/quote/{ticker}?apikey={api_key}"

print(f"\n📡 [Test 1] Quote API 호출 중... ({url_quote})")
try:
    res = requests.get(url_quote, timeout=10)
    print(f"   👉 상태 코드: {res.status_code}")
    print(f"   👉 응답 내용: {res.text[:300]}...") # 너무 길면 자름
except Exception as e:
    print(f"   ❌ 통신 실패: {e}")

# 2. 내부자 거래 (Insider) - 무료 플랜 제한이 잦은 API
url_insider = f"https://financialmodelingprep.com/api/v3/insider-trading/{ticker}?limit=5&apikey={api_key}"

print(f"\n📡 [Test 2] Insider API 호출 중... ({url_insider})")
try:
    res = requests.get(url_insider, timeout=10)
    print(f"   👉 상태 코드: {res.status_code}")
    print(f"   👉 응답 내용: {res.text[:300]}...")
except Exception as e:
    print(f"   ❌ 통신 실패: {e}")