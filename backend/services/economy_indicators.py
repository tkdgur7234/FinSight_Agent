# backend/services/economy_indicators.py

import requests
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------
# [i18n] Translation Dictionary for Output Display
# ---------------------------------------------------------
TRANSLATIONS = {
    "en": {
        "cpi": "Consumer Price Index (CPI)",
        "ppi": "Producer Price Index (PPI)",
        "pce": "Personal Consumption Exp (PCE)",
        "nfp": "Non-Farm Payrolls (NFP)",
        "icsa": "Initial Jobless Claims",
        "retail": "Retail Sales",
        "fomc": "Fed Funds Rate (FOMC)",
        "col_name": "Indicator",
        "col_actual": "Actual",
        "col_ref": "Reference",
        "col_forecast": "Forecast",
        "col_date": "Date (KST)",
        "col_filter": "Filter (Prev Day)",
        "col_impact": "Impact",
        "high": "🔴 High",
        "med": "🟠 Med",
        "low": "🟡 Low",
        # Standard US colors: Usually Green for "better than expected" (even if value is lower, like jobless claims). 
        # For simplicity in this logic matching the original: Higher than expected = Red, Lower = Blue (or Green in US)
        "color_higher": "#c0392b", # Red
        "color_lower": "#27ae60"   # Green
    },
    "ko": {
        "cpi": "소비자물가지수 (CPI)",
        "ppi": "생산자물가지수 (PPI)",
        "pce": "개인소비지출 (PCE)",
        "nfp": "비농업 고용지수 (NFP)",
        "icsa": "신규 실업수당 청구",
        "retail": "소매 판매",
        "fomc": "기준금리 (FOMC)",
        "col_name": "지표명",
        "col_actual": "발표값",
        "col_ref": "기준월",
        "col_forecast": "예상",
        "col_date": "발표일(KST)",
        "col_filter": "필터링(전일 발표)",
        "col_impact": "중요도",
        "high": "🔴 High",
        "med": "🟠 Med",
        "low": "🟡 Low",
        # Standard KR colors: Higher than expected = Red, Lower = Blue
        "color_higher": "#e74c3c", # Red
        "color_lower": "#3498db"   # Blue
    }
}

# 1. Indicator Mapping Configuration
# Added 'trans_key' to dynamically fetch the translated name
INDICATOR_MAP = {
    "CPIAUCSL": {"trans_key": "cpi", "units": "pc1", "suffix": "%", "decimal": 1, "ff_title": "CPI y/y"},
    "PPIFIS":   {"trans_key": "ppi", "units": "pc1", "suffix": "%", "decimal": 1, "ff_title": "PPI m/m"},
    "PCEPI":    {"trans_key": "pce", "units": "pc1", "suffix": "%", "decimal": 1, "ff_title": "Core PCE Price Index m/m"},
    "PAYEMS":   {"trans_key": "nfp", "units": "chg", "suffix": "K", "decimal": 0, "ff_title": "Non-Farm Employment Change"},
    "ICSA":     {"trans_key": "icsa", "units": "lin", "suffix": "K", "divide": 1000, "decimal": 0, "ff_title": "Unemployment Claims"},
    "RSAFS":    {"trans_key": "retail", "units": "pch", "suffix": "%", "decimal": 1, "ff_title": "Retail Sales m/m"},
    "DFEDTARU": {"trans_key": "fomc", "units": "lin", "suffix": "%", "decimal": 2, "ff_title": "Federal Funds Rate"}
}

def get_fred_data():
    """Fetches the latest data from the FRED API."""
    api_key = os.getenv("FRED_API_KEY")
    results = {}
    
    # Load dynamic language setting
    lang_code = os.getenv("REPORT_LANGUAGE", "en").lower()
    t = TRANSLATIONS.get(lang_code, TRANSLATIONS["en"])
    
    for sid, info in INDICATOR_MAP.items():
        try:
            url = f"https://api.stlouisfed.org/fred/series/observations"
            params = {
                "series_id": sid,
                "units": info.get("units"),
                "sort_order": "desc",
                "limit": 1,
                "api_key": api_key,
                "file_type": "json"
            }
            res = requests.get(url, params=params).json()
            
            if "observations" in res and res["observations"]:
                obs = res["observations"][0]
                val = float(obs["value"])
                
                if "divide" in info:
                    val /= info["divide"]
                
                decimal_places = info.get("decimal", 2)
                formatted_num = f"{val:,.{decimal_places}f}"
                
                date_str = obs["date"]
                if sid == 'ICSA':
                    ref_date = date_str[2:] # 25-12-13
                else:
                    ref_date = date_str[2:7] # 25-11
                
                results[info["ff_title"]] = {
                    "name": t[info["trans_key"]], # Dynamic Name
                    "value": val,
                    "display_value": f"{formatted_num}{info['suffix']}",
                    "ref_date": ref_date,
                    "ff_title": info["ff_title"]
                }
        except Exception as e:
            print(f"   ❌ [FRED API] Error ({sid}): {e}")
            
    return results

def get_forex_factory_data():
    """Parses Forex Factory XML (Enhanced whitespace removal)"""
    try:
        url = f"https://nfs.faireconomy.media/ff_calendar_thisweek.xml?t={int(datetime.now().timestamp())}"
        
        # Add User-Agent to prevent occasional blocking
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers)
        
        # XML Parsing
        try:
            root = ET.fromstring(res.content)
        except ET.ParseError:
            print("   ❌ [XML Parse Error] Invalid response from Forex Factory.")
            return []
        
        items = []
        for event in root.findall("event"):
            # Safe text extraction function (Prevents None and removes whitespace)
            def get_text(tag):
                elem = event.find(tag)
                if elem is not None and elem.text:
                    return elem.text.strip() # [Core] Remove leading/trailing whitespace
                return None

            country = get_text("country")
            if country != "USD": continue
            
            title = get_text("title")
            forecast = get_text("forecast") # Forecast might be missing
            date_str = get_text("date")
            time_str = get_text("time")
            impact = get_text("impact")
            
            # Must have title, date, and time to be added (attempt match even without forecast)
            if title and date_str and time_str:
                
                # Parse Date/Time (MM-DD-YYYY, 1:30pm)
                try:
                    mm, dd, yyyy = map(int, date_str.split('-'))
                    
                    time_str = time_str.lower()
                    is_pm = "pm" in time_str
                    is_am = "am" in time_str
                    time_part = time_str.replace("am", "").replace("pm", "").strip()
                    
                    if ":" in time_part:
                        hour, minute = map(int, time_part.split(':'))
                    else:
                        hour, minute = int(time_part), 0
                        
                    if is_pm and hour < 12: hour += 12
                    if is_am and hour == 12: hour = 0
                    
                    # Create UTC time (Assuming NY time -> Add 9 hours for KST)
                    # Note: Depends strictly on XML timezone, but retaining existing JS logic (+9h)
                    dt_obj = datetime(yyyy, mm, dd, hour, minute)
                    kst_time = dt_obj + timedelta(hours=9)
                    
                    kst_full_str = kst_time.strftime("%Y-%m-%d %H:%M")
                    kst_date_str = kst_time.strftime("%Y-%m-%d")
                    
                    # Convert Forecast to Number
                    forecast_val = 0.0
                    if forecast:
                        clean_forecast = forecast.replace('%', '').replace('K', '').strip()
                        try:
                            forecast_val = float(clean_forecast)
                        except:
                            forecast_val = 0.0

                    items.append({
                        "title": title,
                        "forecast_str": forecast if forecast else "-",
                        "forecast_val": forecast_val,
                        "impact": impact if impact else "-",
                        "kst_full_str": kst_full_str,
                        "kst_date_str": kst_date_str
                    })
                    
                    # [Debug] For checking matched titles
                    # print(f"[XML Found] {title} / {date_str}")

                except Exception as e:
                    print(f"   ⚠️ [Date Parse Error] ({title}): {e}")
                    continue

        return items
        
    except Exception as e:
        print(f"   ❌ [Forex Factory Error] {e}")
        return []

def get_economy_indicators():
    """Merges and returns final data based on language settings."""
    
    # Load dynamic language setting
    lang_code = os.getenv("REPORT_LANGUAGE", "en").lower()
    t = TRANSLATIONS.get(lang_code, TRANSLATIONS["en"])
    
    fred_data = get_fred_data() # Dict
    ff_data = get_forex_factory_data() # List
    
    final_list = []
    
    for ff_title, f_item in fred_data.items():
        # [Core] Partial Match
        # Example: "Unemployment Claims" in "Unemployment Claims" -> True
        matched_ff = next((x for x in ff_data if f_item['ff_title'].lower() in x['title'].lower()), None)
        
        # Construct response item using dynamic language keys
        res_item = {
            t["col_name"]: f_item["name"],
            t["col_actual"]: f_item["display_value"],
            t["col_ref"]: f_item["ref_date"],
            t["col_forecast"]: "-",
            t["col_date"]: "-",
            t["col_filter"]: "-",
            t["col_impact"]: "-"
        }
        
        if matched_ff:
            res_item[t["col_forecast"]] = matched_ff["forecast_str"]
            res_item[t["col_date"]] = matched_ff["kst_full_str"]
            res_item[t["col_filter"]] = matched_ff["kst_date_str"]
            
            # Impact Emoji
            imp = matched_ff["impact"]
            if imp == 'High': res_item[t["col_impact"]] = t["high"]
            elif imp == 'Medium': res_item[t["col_impact"]] = t["med"]
            elif imp == 'Low': res_item[t["col_impact"]] = t["low"]
            else: res_item[t["col_impact"]] = imp
            
            # Color coding actual value based on forecast comparison
            # Only apply color if a valid forecast exists
            if matched_ff["forecast_val"] != 0:
                diff = f_item["value"] - matched_ff["forecast_val"]
                
                # Note: Unemployment claims (ICSA) are 'better' when lower. 
                # This logic simply maintains the original: Higher than expected = "color_higher"
                
                if diff > 0: # Higher than expected
                    res_item[t["col_actual"]] = f'<span style="color: {t["color_higher"]};"><b>{f_item["display_value"]}</b></span>'
                elif diff < 0: # Lower than expected
                    res_item[t["col_actual"]] = f'<span style="color: {t["color_lower"]};"><b>{f_item["display_value"]}</b></span>'
                
        final_list.append(res_item)
        
    return final_list

if __name__ == "__main__":
    # Test execution
    print(get_economy_indicators())