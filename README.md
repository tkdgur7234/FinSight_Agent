# FinSight_Agent
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
   1-4. Most Imapct Market News (25-12-31)
   전날 시장에 영향을 끼친 주요 뉴스들을 수집 및 제시
   LLM을 통해 핵심 재료를 요약해서 제시
   - Google News RSS 이용해 크롤링 하되 세 단계 전략(현상, 원인, 주도주)으로 나눠서 수집
   - 수집된 기사들 중 중복을 고려해 URL 기준 중복 제거
   - RSS의 title + description만 LLM에 넘겨줘서 토큰 절약
   
2. 관심 종목 집중 모니터링
   2-1. 관심 종목 커뮤니티 감성 분석
   레딧을 크롤링하고 ai 활용해 유저들의 종목별 공포 탐욕 지수 확인, 의미있는 게시물만 요약 후 제공
   2-2. 관심 종목 뉴스
   2-3. 관심 종목 이상 거래 감지(내부자나 대규모 매수/매도)
   
3. 공시 기반 리스크 모니터링 기능

4. 주간 핫한 테마, 종목 요약 브리핑 시스템 (토요일만 보고)

+ 전일 휴장이었던 날에 대해서는 어떻게 처리할지 고민해보기
+ 
----------------------------------------------
수정 (25-12-21)
기존에는 n8n으로 모두 작업.
         ↓
시스템의 유연성과 확장성을 위해,
데이터 분석 및 리포트 렌더링 로직은 서버를 구축해(FastAPI) 마이크로서비스화 하였으며, 
워크플로우 제어는 n8n을 사용하여 로직과 오케스트레이션을 분리.

my-stock-portfolio/
├── backend/               # Python 서버 (FastAPI)
│   ├── main.py            # 메인 서버 코드
│   ├── routers/           
│   ├── services/          # 각종 기능 구현 코드
│   ├── templates/         # 웹 시각화 html
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
4. ".env" 환경변수 파일 생성
api 키 입력
5. 서버 실행
uvicorn main:app --reload

----------------------------
Daily Create Function

(25-12-23) 
(1) 1-1. Index 구현
(2) 1-2. S&P 500 map 구현
(3) 1-3. Economy_indicators 구현

(25-12-24)
(1) 1-4. Most Imapct Market News 구현
뉴스 검색 시간대 개선 및 llm prompt 개선 작업 중

(25-12-31)
(1) 1-4. Most Imapct Market News
뉴스 정확도 개선

(25-1-2)
(1) 1-1. Index
원달러 환율 추가
(2) 1번 기능 조합해서 웹페이지 구현
(3) 2, 3번 기능 2번으로 통합 후 소주제 분할
(4) 계획서에 관심 종목 뉴스 기능 추가

<img width="633" height="903" alt="image" src="https://github.com/user-attachments/assets/1fbfddba-7815-4450-9f2a-4b9c51319921" />
<img width="639" height="829" alt="image" src="https://github.com/user-attachments/assets/9ba404bc-0b10-4627-8b97-f3e2e3767078" />

