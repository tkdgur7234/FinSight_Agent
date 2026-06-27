# backend/main.py

from fastapi import FastAPI
import yfinance as yf
from datetime import datetime
import pandas as pd
import math
import os
from dotenv import load_dotenv
from routers import report


# 1. Load environment variables
load_dotenv()

# [Added] Print the current language mode to the terminal for verification
current_lang = os.getenv("REPORT_LANGUAGE", "en")
print(f"🌍 Server starting... Report Language Mode: {current_lang.upper()}")

app = FastAPI()

# Register router
app.include_router(report.router)

# ---------------------------------------------------------
# [Core] Cleaner function to prevent JSON serialization errors
# Finds NaN (Not a Number) in the data and replaces it with None (null)
# ---------------------------------------------------------
def clean_data(data):
    if isinstance(data, dict):
        return {k: clean_data(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [clean_data(v) for v in data]
    elif isinstance(data, float):
        if math.isnan(data) or math.isinf(data):
            return None  # Replace NaN or infinity with None
    return data
# ---------------------------------------------------------

@app.get("/")
def health_check():
    return {"status": "ok", "message": "Server running with Router pattern!"}

@app.post("/StockMarket_Auto_Reporter")
def get_StockMarket_Auto_Reporter():
    start_time = datetime.now()
    print(f"[{start_time}] 🚀 Data request received! Processing started...")

    target_tickers = {
        'S&P500': '^GSPC', 
        'Nasdaq': '^IXIC',
        'Bitcoin': 'BTC-USD' 
    }
    
    symbols = list(target_tickers.values())
    result = {}

    try:
        # Execute yf.download
        df = yf.download(symbols, period="2d", group_by='ticker', threads=True, progress=False, auto_adjust=False)

        for name, symbol in target_tickers.items():
            try:
                # 1. Extract data
                if len(symbols) > 1:
                    data = df[symbol]
                else:
                    data = df
                
                # 2. Validation and calculation
                if not data.empty:
                    # Find column name ('Close' or 'Adj Close')
                    if 'Close' in data.columns:
                        price_col = 'Close'
                    elif 'Adj Close' in data.columns:
                        price_col = 'Adj Close'
                    else:
                        price_col = data.columns[-1]

                    last_close = float(data[price_col].iloc[-1])
                    prev_close = float(data[price_col].iloc[-2]) if len(data) >= 2 else last_close
                    
                    # Calculate change rate
                    if prev_close != 0:
                        change_rate = ((last_close - prev_close) / prev_close) * 100
                    else:
                        change_rate = 0.0
                    
                    result[name] = {
                        "price": round(last_close, 2),
                        "change": f"{round(change_rate, 2)}%"
                    }
                else:
                    result[name] = {"error": "No Data"}
            except Exception as parse_error:
                print(f"Error parsing {name}: {parse_error}")
                result[name] = {"error": "Parse Error"}

        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        print(f"[{end_time}] ✅ Processing complete! (Duration: {duration} sec)")

        # 3. Construct response data
        response_data = {
            "timestamp": end_time.strftime("%Y-%m-%d %H:%M:%S"),
            "data": result,
            "performance": f"{duration} sec",
            "message": "Data collection successful"
        }

        # [Important] Run cleaner before returning (NaN -> None)
        return clean_data(response_data)

    except Exception as e:
        print(f"Server Error: {e}")
        return {"status": "error", "message": str(e)}