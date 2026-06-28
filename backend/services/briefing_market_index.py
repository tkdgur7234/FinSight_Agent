# backend/services/briefing_market_index.py

import yfinance as yf
import pandas as pd
import requests
from bs4 import BeautifulSoup
import os
import base64
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------
# [i18n] Translation Dictionary for Output Display
# ---------------------------------------------------------
TRANSLATIONS = {
    "en": {
        "dow": "Dow Jones",
        "sp500": "S&P 500",
        "nasdaq": "Nasdaq",
        "russell": "Russell 2000",
        "wti": "WTI Crude",
        "gold": "Gold",
        "btc": "Bitcoin",
        "us10y": "US 10Y Bond",
        "usdkrw": "Dollar Index / Exchange Rate",
        "header_index": "Index",
        "header_price": "Price",
        "header_change": "Change %",
        "up_emoji": "🟢",  # US standard (Green for Up)
        "down_emoji": "🔴", # US standard (Red for Down)
        "krw_suffix": " KRW",
        "err_ticker": "⚠️ Ticker Error",
        "err_col": "⚠️ No Column",
        "err_data": "⚠️ No Data"
    },
    "ko": {
        "dow": "다우 존스",
        "sp500": "S&P 500",
        "nasdaq": "나스닥",
        "russell": "러셀 2000",
        "wti": "WTI 원유",
        "gold": "금",
        "btc": "비트코인",
        "us10y": "미 국채 10년",
        "usdkrw": "달러 인덱스 / 환율",
        "header_index": "지표",
        "header_price": "현재가",
        "header_change": "변동률",
        "up_emoji": "🔴",  # KR standard (Red for Up)
        "down_emoji": "🔵", # KR standard (Blue for Down)
        "krw_suffix": "원",
        "err_ticker": "⚠️ 티커 오류",
        "err_col": "⚠️ 컬럼 없음",
        "err_data": "⚠️ 데이터 없음"
    }
}

# Define targets securely to map with translations
TARGET_INDICES = [
    ("dow", "^DJI"),
    ("sp500", "^GSPC"),
    ("nasdaq", "^IXIC"),
    ("russell", "^RUT"),
    ("wti", "CL=F"),
    ("gold", "GC=F"),
    ("btc", "BTC-USD"),
    ("us10y", "^TNX"),
    ("usdkrw", "DX-Y.NYB")
]

def get_naver_usd_rate():
    """
    Crawls the real-time USD/KRW exchange rate from Naver Finance.
    """
    try:
        url = "https://finance.naver.com/marketindex/"
        # Anti-bot header
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")
            # Extract 'USD' value from Naver Finance
            usd_item = soup.select_one("#exchangeList > li.on > a.head.usd > div > span.value")
            if usd_item:
                # Remove comma and convert to float
                return float(usd_item.text.replace(",", ""))
    except Exception as e:
        print(f"   ❌ [Naver Crawl] Error fetching exchange rate: {e}")
    
    return 0.0 # Return 0.0 on failure

def get_market_summary_markdown():
    """
    Generates a Markdown table summarizing major market indices.
    Restored to match the original 3-column layout and calculation logic.
    """
    # [Language Setup]
    lang_code = os.getenv("REPORT_LANGUAGE", "en").lower()
    t = TRANSLATIONS.get(lang_code, TRANSLATIONS["en"])
    
    symbols = [item[1] for item in TARGET_INDICES]
    
    print("📊 [Market Index] Downloading yfinance data...")
    # Download yfinance data
    df = yf.download(symbols, period="5d", group_by='ticker', threads=True, progress=False, auto_adjust=False)

    rows = []
    
    # [Step 1] Fetch exchange rate from Naver
    krw_rate = get_naver_usd_rate()

    # [Step 2] Loop to generate table rows
    for key_name, symbol in TARGET_INDICES:
        display_name = t[key_name]
        
        try:
            if len(symbols) > 1:
                try:
                    data = df[symbol]
                except KeyError:
                    rows.append(f"| {display_name} | N/A | {t['err_ticker']} |")
                    continue
            else:
                data = df

            # Find valid column
            cols = [c.lower() for c in data.columns]
            target_col = None
            if 'close' in cols:
                target_col = data.columns[cols.index('close')]
            elif 'adj close' in cols:
                target_col = data.columns[cols.index('adj close')]
            
            if target_col is None:
                rows.append(f"| {display_name} | N/A | {t['err_col']} |")
                continue

            # Filter valid series
            valid_series = data[target_col].dropna()

            if valid_series.empty:
                rows.append(f"| {display_name} | N/A | {t['err_data']} |")
                continue

            last_close = float(valid_series.iloc[-1])
            
            if len(valid_series) >= 2:
                prev_close = float(valid_series.iloc[-2])
            else:
                prev_close = last_close

            change_amt = last_close - prev_close
            change_pct = (change_amt / prev_close) * 100 if prev_close != 0 else 0.0

            # Dynamic Emoji & Sign based on language preference
            emoji = t["up_emoji"] if change_pct >= 0 else t["down_emoji"]
            sign = "+" if change_pct >= 0 else ""
            
            # Custom formatting based on asset type
            if symbol == "DX-Y.NYB":
                price_str = f"{last_close:.2f} / {krw_rate:,.2f}{t['krw_suffix']}"
            elif symbol == "^TNX":
                price_str = f"{last_close:.3f}"
            elif symbol == "BTC-USD":
                price_str = f"{last_close:,.0f}"
            else:
                price_str = f"{last_close:,.2f}"

            rows.append(f"| {display_name} | {price_str} | {emoji} {sign}{change_pct:.2f}% |")

        except Exception as e:
            print(f"   ❌ [Market Index] Error processing {display_name}: {e}")
            rows.append(f"| {display_name} | Error | ⚠️ Error |")

    # Construct the exact original header format dynamically
    header = f"| {t['header_index']} | {t['header_price']} | {t['header_change']} |\n| :--- | :---: | :---: |"
    
    print("   ✅ [Market Index] Markdown generation complete.")
    return header + "\n" + "\n".join(rows)


def get_sp500_map_image():
    """
    Captures the S&P 500 Heatmap via ApiFlash and returns it as a Base64 string.
    """
    access_key = os.getenv("APIFLASH_ACCESS_KEY")
    if not access_key: 
        print("   ⚠️ [S&P 500 Map] Missing APIFLASH_ACCESS_KEY in environment variables.")
        return None
    
    print("🌎 [S&P 500 Map] Requesting heatmap image from ApiFlash...")
    
    url = "https://api.apiflash.com/v1/urltoimage"
    params = {
        "access_key": access_key,
        "url": "https://finviz.com/map.ashx?t=sec",
        "element": "#canvas-wrapper",
        "response_type": "image",
        "format": "png",
        "quality": 100,
        "width": 1920,
        "height": 1080,
        "wait_until": "page_loaded"
    }

    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        print("   ✅ [S&P 500 Map] Successfully captured and encoded image.")
        return base64.b64encode(response.content).decode("utf-8")
    except Exception as e:
        print(f"   ❌ [S&P 500 Map] ApiFlash Error: {e}")
        return None