# StockMarket_Auto_Reporter.n8n
주식 시장 종합 분석 리포팅 시스템

기능
1. 각종 지표, 경제 뉴스 기반 데일리 시황 리포트
   1-1. Index (25-12-08)
   s&p, nasdaq, russel, bitcoin, ... 등의 일간 변동값
   1-2. S&P 500 Map (25-12-08)
   apiflash 사이트를 통해 s&p 500 map 캡쳐해서 불러오기
   1-3. Released main economy_indicators (25-12-21)
   소비자물가지수(CPI), 생산자물가지수(PPI), 개인소비지출(PCE), 비농업고용지수(NFP), 신규실업수당청구, 소매판매, 기준금리(FOMC)와 같은
   전일 발표되는 주요 지표를 fred api와 forex factory 크롤링을 통해 포스팅    
   1-4. Most Imapct Market News (25-12-24)
   전날 시장에 영향을 끼친 주요 뉴스들을 수집 및 제시
   LLM을 통해 핵심 재료를 요약해서 제시
   - Google News RSS 이용해 크롤링 하되  세 단계 전략(현상, 원인, 주도주)으로 나눠서 수집
   - 수집된 기사들 중 중복을 고려해 URL 기준 중복 제거
   - RSS의 title + description만 LLM에 넘겨줘서 토큰 절약
   1-5. 
   위 내용을 토대로 전날 증시에 대한 AI 요약 및 분석 
   
3. 관심 종목 커뮤니티 감성 분석 기능
   레딧을 크롤링하고 ai 활용해 유저들의 종목별 공포 탐욕 지수 확인, 의미있는 게시물만 요약 후 제공

4. 관심 종목 이상 거래 감지 알람 
   
5. 공시 기반 리스크 모니터링 기능

6. 주간 핫한 테마, 종목 요약 브리핑 시스템 (토요일만 보고)


----------------------------------------------
수정 (25-12-21)
기존에는 n8n으로 모두 작업.

시스템의 유연성과 확장성을 위해,
데이터 분석 및 리포트 렌더링 로직은 서버를 구축해(FastAPI) 마이크로서비스화 하였으며, 
워크플로우 제어는 n8n을 사용하여 로직과 오케스트레이션을 분리.

my-stock-portfolio/
├── backend/               # Python 서버 (FastAPI)
│   ├── main.py            # 메인 서버 코드
│   ├── requirements.txt   # 라이브러리 목록
│   └── .venv/             # 가상 환경
└── n8n/                   # n8n 관련 파일 (Docker 등)

(25-12-22)
1. n8n 설치 위치와 클론된 폴더 위치가 맞아야함.
2. 가상환경 생성 및 접속
python -m venv venv
source venv/bin/activate
3. 라이브러리 내려받기
pip install -r requirements.txt
4. 서버 실행
uvicorn main:app --reload

----------------------------
Daily Create Function

(25-12-23) 
1-1. Index
1-2. S&P 500 map
1-3. Economy_indicators

(25-12-24)
1-4. Most Imapct Market News
구현 완료
뉴스 검색 시간대 개선 및 llm prompt 개선 작업 중
