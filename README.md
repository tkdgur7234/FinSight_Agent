# 📈 FinSight_Agent
> Intelligence agent for US stock market data collection, whale trade tracking, and daily briefing automation.

🌍 **[Read in Korean](./README_ko.md)**

## ✨ Features
<table>
<tr>
<td width="33%">

### 📊 Market Intelligence

- Global Index
- Economic Indicators
- AI News Summary
- S&P500 Heatmap

</td>

<td width="33%">

### 🎯 Watchlist

- Community Sentiment
- Stock News
- Opinion Summary

</td>

<td width="33%">

### 🚨 Smart Detection

- Unusual Volume
- Insider Trading
- AI Reporting

</td>
</tr>
</table>


* **Market & Macroeconomic Indicator Monitoring**: Automatic tracking of key economic indicators using the FRED API and other sources.
* **Anomaly Trade Detection (Whale & Insider)**: Accumulates whale (large capital) and corporate insider transaction details in a database (`whale_tracker.db`) and monitors significant volatility.
* **News & Community Sentiment Analysis**: Crawls stock market news and communities (Reddit) and analyzes market sentiment using LLM.
* **Customized Daily Briefing**: Automatically generates HTML template-based reports based on collected data and sends them via Slack and Email.

## 🛠 Tech Stack

**Tech Stack** <br>
<img src="https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white">
<img src="https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white">
<img src="https://img.shields.io/badge/n8n-FF6D5A?style=for-the-badge&logo=n8n&logoColor=white">
<img src="https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white">
<img src="https://img.shields.io/badge/SQLite-003B57?style=for-the-badge&logo=sqlite&logoColor=white">

**AI & Data** <br>
<img src="https://img.shields.io/badge/Upstage_Solar_Pro-000000?style=for-the-badge">
<img src="https://img.shields.io/badge/yfinance-FF9900?style=for-the-badge">
<img src="https://img.shields.io/badge/FRED_API-005288?style=for-the-badge">

## 🏗 Architecture Diagram
<img src="./docs/images/diagram.png" width="800" alt="Architecture Diagram">

## 📂 Project Structure
```text
FinSight_Agent/
├── backend/
│   ├── main.py                 # FastAPI application entry point
│   ├── services/               # Business logic (whale_tracker, insider_tracker, etc.)
│   ├── templates/              # report_template.html (Briefing report template)
│   ├── whale_tracker.db        # Local database
│   └── docker-compose.yml      # Docker container configuration
├── docs/                       # Development logs and diagram images
└── n8n_daily_briefing.json     # Automated workflow configuration file
```

## 🚀 Installation
**1. Clone the repository**
```bash
git clone [https://github.com/tkdgur7234/finsight_agent.git]
cd finsight_agent/backend 
```
**2. Set up environment variables** (Refer to .env.example)
```bash
cp .env.example .env  # Enter FRED_API_KEY, SLACK_WEBHOOK_URL, etc. in the .env file.
```
**3. Run backend via Docker**
```bash
docker-compose up -d --build
```
**4. Run server locally**
```bash
uvicorn main:app --reload --host 0.0.0
```

## ⚙ Usage
* **Check API Documentation**: After running the backend server, access `http://localhost:8000/docs` to test each endpoint via the FastAPI Swagger UI.
* **n8n Integration**: Import the provided `n8n_daily_briefing.json` workflow into your n8n environment to activate daily briefing scheduling.

## 🏆 Key Achievements

<table>
<tr>

<td width="33%" valign="top">

### 🚀 Performance

⚡ **90% Faster**

Data Collection

**3 min → 15 sec**

</td>

<td width="33%" valign="top">

### 🤖 Autonomous Agent

Automated Workflow

**Collect → Analyze → Report → Email**

End-to-End AI Pipeline

</td>

<td width="33%" valign="top">

### 🧠 AI Intelligence

LLM-powered

News Summarization

Market Sentiment Analysis

Market Insight Generation

</td>

</tr>

<tr>

<td width="33%" valign="top">

### 📊 Quantitative Analysis

Z-score-based

Unusual Volume Detection

Insider Trading Analysis

</td>

<td width="33%" valign="top">

### 🌎 Multi-source Intelligence

Economic Indicators

Market News

Community Sentiment

Market Data

Integrated Analysis

</td>

<td width="33%" valign="top">

### 💡 Efficient Pipeline

Duplicate Removal

Time-based Filtering

LLM Token Optimization

High-quality Data Processing

</td>

</tr>

</table>

## 📊 Results

### 📈 Daily Market Briefing

A daily report combining market indices, economic indicators, and AI-generated insights.

<p align="center">
  <img src="docs/images/daily-market-report.png" width="50%">
</p>

---

### 🎯 Watchlist Monitoring

Tracks watchlist stocks using community sentiment and curated market news.

<p align="center">
  <img src="docs/images/heatmap.png" width="50%">
  <img src="docs/images/watchlist.png" width="50">
</p>

---

### 🚨 Smart Detection

Detects unusual trading activity and insider transactions using quantitative analysis.

<p align="center">
  <img src="docs/images/anomaly-detection.png" width="50%">
  <img src="docs/images/insider-trading.png" width="50%">
</p>


## 💡 Trouble Shooting
  
* **API Bottleneck Improvement & Crawling Logic Optimization (Multi-threading)**
  * **Issue**: Two major problems occurred in the insider trading monitoring logic for major stocks. First, the website crawling criteria were set too strictly, causing data tangling on the site. Second, sequentially communicating with the `yfinance` server for filtered tickers (approx. 300) resulted in a severe performance bottleneck of over 3 minutes.
  * **Solution**: Completely revamped the data collection and processing methods.
    1. **Separation of Filtering Logic**: Instead of relying on the site's search criteria, 5,000 data points were initially collected using loose criteria, followed by strict secondary filtering (recent 1 month) within the Python logic to ensure data integrity.
    2. **Application of Multi-threading**: Built a multi-threaded environment using `ThreadPoolExecutor`to significantly reduce I/O wait times during external API communication.
  * **Result**: Safely collected data while dramatically reducing the execution time from **3 minutes to approximately 15 seconds (about 90% performance improvement).** 
   <img src="./docs/images/multithreading.png" width="800" alt="TroubleShooting">

* **Data Source Dependency Resolution (FMP API → Finviz Crawling)**
  * **Issue**: During the initial construction of the data pipeline for 'Anomaly Trade Detection', the `Financial Modeling Prep(FMP) API` was utilized. However, a sudden policy change by the service halted data collection.
  * **Solution**: To reduce external API dependency and ensure service stability, the logic was fully modified to independently crawl (using BeautifulSoup) and parse data from the `Finviz`website, successfully restoring the data collection pipeline.


## 📈 Future Improvements

* **Advanced Market Volatility Monitoring**: Add monitoring capabilities for Short Interest and potential short squeeze occurrences.
* **Personalization & Monetization Model**: Introduce a feature to filter and report customized data tailored to users' individual Watchlists.
* **Email Delivery System Stabilization**: Transition from the current Google App Password SMTP method to integrating professional email services like SendGrid or Mailgun, replacing the n8n node to secure transmission stability.
* **Report UI/UX Improvement**: Consider replacing the current mobile-optimized email body reporting with a Notion or separate web link format for a more comfortable viewing experience in web environments.