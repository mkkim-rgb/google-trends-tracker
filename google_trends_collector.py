#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
구글 트렌드 트래커 — 브랜드×국가 월간/주간 수집 → 구글시트 기록
Naver Keyword Estimator TF / Google 확장

설계:
  - 월간: timeframe "2023-01-01 ~ 오늘" (긴 기간 → 월 단위), 상대지수 × N(쿼리지수) = 계산값
  - 주간: timeframe "today 12-m" (1년 → 주 단위), 상대지수만. KR은 시작주+1(월요일) 컬럼 추가
  - 설정탭에서 (브랜드/나라/키워드/쿼리지수N) 읽어 동작
  - 탭 없으면 자동 생성. 매 실행 = 해당 탭 전체 새로고침(덮어쓰기)

환경변수:
  SHEET_ID            대상 구글시트 ID
  GOOGLE_SA_FILE      서비스계정 JSON 경로 (기본 service_account.json)
  CONFIG_TAB          설정탭 이름 (기본 '설정')
"""
import os, sys, time, datetime
import gspread
from pytrends.request import TrendReq

SHEET_ID    = os.environ["SHEET_ID"]
SA_FILE     = os.environ.get("GOOGLE_SA_FILE", "service_account.json")
CONFIG_TAB  = os.environ.get("CONFIG_TAB", "설정")
TZ_BY_GEO   = {"KR": 540, "JP": 540, "US": -300}
MONTHLY_START = "2023-01-01"
PAUSE = 12          # pytrends 호출 간격(초) — 429 회피
MAX_RETRY = 4

def log(*a): print(*a, flush=True)

def trends(geo):
    tz = TZ_BY_GEO.get(geo, 0)
    return TrendReq(hl="en-US", tz=tz, retries=2, backoff_factor=1.0)

def fetch(keyword, geo, timeframe):
    """429 재시도 포함 상대지수 시계열 반환: [(date, value), ...]"""
    for attempt in range(MAX_RETRY):
        try:
            pt = trends(geo)
            pt.build_payload([keyword], timeframe=timeframe, geo=geo)
            df = pt.interest_over_time()
            if df is None or df.empty or keyword not in df.columns:
                return []
            return [(idx.date(), int(row[keyword])) for idx, row in df.iterrows()]
        except Exception as e:
            wait = PAUSE * (attempt + 1)
            log(f"  ! {keyword}/{geo} 재시도({attempt+1}) {type(e).__name__}: {e} → {wait}s 대기")
            time.sleep(wait)
    log(f"  !! {keyword}/{geo} 실패(최대 재시도 초과)")
    return []

def ensure_tab(sh, name, headers):
    try:
        ws = sh.worksheet(name)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=name, rows=200, cols=max(6, len(headers)))
        log(f"  + 탭 생성: {name}")
    return ws

def write_tab(ws, headers, rows):
    ws.clear()
    # gspread 6.x 시그니처: range_name 먼저
    ws.update(values=[headers] + rows, range_name="A1", value_input_option="USER_ENTERED")

def main():
    today = datetime.date.today().isoformat()
    gc = gspread.service_account(filename=SA_FILE)
    sh = gc.open_by_key(SHEET_ID)
    cfg = sh.worksheet(CONFIG_TAB).get_all_records()  # [{브랜드,나라,키워드,쿼리지수}, ...]
    log(f"설정 {len(cfg)}행 로드, 오늘={today}")

    for r in cfg:
        brand = str(r.get("브랜드", "")).strip()
        geo   = str(r.get("나라", "")).strip().upper()
        kw    = str(r.get("키워드", "")).strip()
        try:
            N = float(r.get("쿼리지수", 0) or 0)
        except ValueError:
            N = 0.0
        if not (brand and geo and kw):
            continue
        prefix = f"{brand}_{geo}"
        log(f"[{prefix}] '{kw}' (N={N})")

        # ── 월간: 2023-01 ~ 오늘, 월 단위, ×N ──
        m = fetch(kw, geo, f"{MONTHLY_START} {today}")
        time.sleep(PAUSE)
        m_rows = [[d.strftime("%Y-%m"), kw, v, round(v * N)] for d, v in m]
        ws = ensure_tab(sh, f"{prefix}_월간", ["년월", "키워드", "상대지수", "계산값"])
        write_tab(ws, ["년월", "키워드", "상대지수", "계산값"], m_rows)
        log(f"  월간 {len(m_rows)}행")

        # ── 주간: 최근 1년, 주 단위, 상대지수만 (KR은 시작주+1) ──
        w = fetch(kw, geo, "today 12-m")
        time.sleep(PAUSE)
        if geo == "KR":
            headers = ["시작주", "키워드", "상대지수", "시작주+1"]
            w_rows = [[d.isoformat(), kw, v, (d + datetime.timedelta(days=1)).isoformat()] for d, v in w]
        else:
            headers = ["시작주", "키워드", "상대지수"]
            w_rows = [[d.isoformat(), kw, v] for d, v in w]
        ws = ensure_tab(sh, f"{prefix}_주간", headers)
        write_tab(ws, headers, w_rows)
        log(f"  주간 {len(w_rows)}행")

    log("완료")

if __name__ == "__main__":
    sys.exit(main())
