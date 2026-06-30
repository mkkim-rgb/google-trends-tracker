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
import os, sys, time, datetime, json
import gspread
from pytrends.request import TrendReq

SHEET_ID    = os.environ["SHEET_ID"]
SA_FILE     = os.environ.get("GOOGLE_SA_FILE", "service_account.json")
CONFIG_TAB  = os.environ.get("CONFIG_TAB", "설정")
ONLY_COUNTRY = os.environ.get("COUNTRY", "").strip().upper()  # 설정 시 그 나라만 처리(나라별 분산 실행)
MODE = os.environ.get("MODE", "both").strip().lower()        # weekly / monthly / both(기본)
TZ_BY_GEO   = {"KR": 540, "JP": 540, "US": -300}
MONTHLY_FETCH_START = "2020-01-01"   # 받아오기 시작(2020~현재=6.5년 >5년 → 월단위)
MONTHLY_KEEP_FROM   = "2023-01"      # 기록은 이 월(YYYY-MM)부터
WEEKLY_START        = "2023-01-01"   # 주간 시작일(이날~현재, <5년이라 주단위 유지)
PAUSE = 20          # pytrends 호출 간격(초) — 429 회피
KW_PAUSE = 30       # 키워드 사이 추가 대기
MAX_RETRY = 5

def log(*a): print(*a, flush=True)

def trends(geo):
    # retries/backoff_factor 안 넘김 — pytrends가 urllib3 Retry(method_whitelist)로 깨짐.
    # 재시도는 fetch()가 직접 처리.
    tz = TZ_BY_GEO.get(geo, 0)
    return TrendReq(hl="en-US", tz=tz)

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

def resolve_query(kw, typ):
    """유형이 '주제'면 구글 엔티티(Topic) MID로 변환. 검색어면 그대로.
       반환: (질의어, 표시정보)"""
    if "주제" not in typ:        # '주제','주제어','주제(Topic)' 등 모두 주제로 인식
        return kw, "검색어"
    if kw.startswith("/g/") or kw.startswith("/m/"):   # 이미 MID 직접 입력
        return kw, f"주제(MID직접:{kw})"
    try:
        sug = TrendReq(hl="en-US").suggestions(kw)   # 엔티티 후보
        time.sleep(5)
        if sug:
            s = sug[0]
            return s["mid"], f"주제:{s['title']}({s['type']}) {s['mid']}"
    except Exception as e:
        log(f"    주제 조회 실패({kw}): {e}")
    log(f"    주제 못 찾음({kw}) → 검색어로 폴백")
    return kw, "주제실패→검색어폴백"

def fetch_multi(qterms, geo, timeframe):
    """여러 질의어를 '한 번에' 조회(같은 정규화=서로 비교 가능). 최대 5개.
       반환: {질의어: [(date, value)]}"""
    qterms = qterms[:5]
    for attempt in range(MAX_RETRY):
        try:
            pt = trends(geo)
            pt.build_payload(qterms, timeframe=timeframe, geo=geo)
            df = pt.interest_over_time()
            out = {}
            for q in qterms:
                if df is not None and not df.empty and q in df.columns:
                    out[q] = [(i.date(), int(r[q])) for i, r in df.iterrows()]
                else:
                    out[q] = []
            return out
        except Exception as e:
            wait = PAUSE * (attempt + 1)
            log(f"  ! 그룹조회/{geo} 재시도({attempt+1}) {type(e).__name__}: {e} → {wait}s 대기")
            time.sleep(wait)
    log(f"  !! 그룹조회/{geo} 실패(최대 재시도 초과)")
    return {q: [] for q in qterms}

def ensure_tab(sh, name, headers):
    try:
        ws = sh.worksheet(name)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=name, rows=200, cols=max(6, len(headers)))
        log(f"  + 탭 생성: {name}")
    return ws

def write_tab(ws, headers, rows):
    ws.clear()
    # RAW: 시트가 '2026-04' 등을 날짜로 멋대로 해석하지 않게(년월/시작주 텍스트 보존).
    # 숫자는 int로 넘기므로 RAW여도 숫자로 저장됨.
    ws.update(values=[headers] + rows, range_name="A1", value_input_option="RAW")

def _coerce(row):
    """시트에서 읽은 문자열 행의 숫자칸(상대지수·계산값)을 int로, 날짜칸은 그대로."""
    out = list(row)
    for i in (2, 3):
        if len(out) > i:
            try:
                out[i] = int(out[i])
            except (ValueError, TypeError):
                pass
    return out

def existing_by_kw(ws):
    """탭 기존 내용을 {키워드: [행,...]}로. 실패 키워드 데이터 보존용."""
    out = {}
    try:
        vals = ws.get_all_values()
    except Exception:
        return out
    for row in vals[1:]:
        if len(row) >= 2 and row[1]:
            out.setdefault(row[1], []).append(_coerce(row))
    return out

def apply_calc_formulas(sh):
    """월간 탭 계산값(D)을 '상대지수 × 설정N' 수식으로 — 설정 N 바꾸면 자동 반영(재실행 불필요)."""
    for ws in sh.worksheets():
        if not ws.title.endswith("_월간"):
            continue
        country = ws.title.split("_")[1]   # UN_US_월간 → US
        n = len(ws.get_all_values())
        if n < 2:
            continue
        formulas = [[f"=C{r}*SUMIFS('{CONFIG_TAB}'!$D:$D,'{CONFIG_TAB}'!$B:$B,\"{country}\",'{CONFIG_TAB}'!$C:$C,B{r})"]
                    for r in range(2, n + 1)]
        ws.update(range_name=f"D2:D{n}", values=formulas, value_input_option="USER_ENTERED")
        log(f"  계산값 수식: {ws.title} {n-1}행")

def main():
    today_d = datetime.date.today()
    today = today_d.isoformat()
    keep_until = (today_d.replace(day=1) - datetime.timedelta(days=1)).strftime("%Y-%m")  # 전월(YYYY-MM): 진행중인 이번달 제외
    do_month = MODE in ("both", "monthly")
    do_week  = MODE in ("both", "weekly")
    log(f"MODE={MODE} (월간={do_month}/주간={do_week}), 월간 기록범위 {MONTHLY_KEEP_FROM}~{keep_until}")
    with open(SA_FILE, encoding="utf-8-sig") as f:   # BOM 견디게
        sa_info = json.load(f)
    gc = gspread.service_account_from_dict(sa_info)
    sh = gc.open_by_key(SHEET_ID)
    cfg = sh.worksheet(CONFIG_TAB).get_all_records()  # [{브랜드,나라,키워드,쿼리지수}, ...]
    log(f"설정 {len(cfg)}행 로드, 오늘={today}")

    # 설정을 brand_country(prefix)로 그룹화 — 한 탭에 여러 키워드 누적
    groups, order = {}, []
    for r in cfg:
        r = {(k.strip() if isinstance(k, str) else k): v for k, v in r.items()}  # 헤더 공백 방어
        brand = str(r.get("브랜드", "")).strip()
        geo   = str(r.get("나라", "")).strip().upper()
        kw    = str(r.get("키워드", "")).strip()
        typ   = str(r.get("유형", "")).strip() or "검색어"   # 검색어 / 주제 (기본 검색어)
        try:
            N = float(r.get("쿼리지수", 0) or 0)
        except ValueError:
            N = 0.0
        if not (brand and geo and kw):
            continue
        if ONLY_COUNTRY and geo != ONLY_COUNTRY:   # 나라별 분산 실행
            continue
        prefix = f"{brand}_{geo}"
        if prefix not in groups:
            groups[prefix] = {"geo": geo, "items": []}; order.append(prefix)
        groups[prefix]["items"].append((kw, N, typ))

    for prefix in order:
        geo, items = groups[prefix]["geo"], groups[prefix]["items"]
        log(f"[{prefix}] 키워드 {len(items)}개")
        hdr_m = ["년월", "키워드", "상대지수", "계산값"]
        hdr_w = ["시작주", "키워드", "상대지수", "시작주+1"] if geo == "KR" else ["시작주", "키워드", "상대지수"]
        ws_m = ensure_tab(sh, f"{prefix}_월간", hdr_m)
        ws_w = ensure_tab(sh, f"{prefix}_주간", hdr_w)
        prev_m, prev_w = existing_by_kw(ws_m), existing_by_kw(ws_w)  # 실패 키워드 보존용
        m_by_kw, w_by_kw = {}, {}

        # 키워드별 질의어 해석(검색어/주제)
        resolved = []
        for kw, N, typ in items:
            qterm, info = resolve_query(kw, typ)
            log(f"  - '{kw}' (N={N}, {info}) → {qterm}")
            resolved.append((kw, N, qterm))

        # 5개씩 '같이' 조회 — 같은 정규화(서로 비교 가능), 그룹당 호출 1회씩
        for ci in range(0, len(resolved), 5):
            chunk = resolved[ci:ci + 5]
            if len(resolved) > 5:
                log(f"  ※ 5개 초과 → {ci//5+1}번째 묶음만 같은 스케일 (묶음 간 비교 불가)")
            qterms = [q for _, _, q in chunk]
            mdata = fetch_multi(qterms, geo, f"{MONTHLY_FETCH_START} {today}") if do_month else {}  # 월간 동시
            if do_month:
                time.sleep(PAUSE)
            wdata = fetch_multi(qterms, geo, f"{WEEKLY_START} {today}") if do_week else {}           # 주간 동시(2023-01~)
            if do_week:
                time.sleep(KW_PAUSE)
            for kw, N, qterm in chunk:
                if do_month:
                    # 월간: MONTHLY_KEEP_FROM ~ 전월(keep_until)만 = 진행중인 이번달 제외(안정)
                    m = mdata.get(qterm, [])
                    rows = [[d.strftime("%Y-%m"), kw, v, round(v * N)]
                            for d, v in m if MONTHLY_KEEP_FROM <= d.strftime("%Y-%m") <= keep_until]
                    if rows:
                        m_by_kw[kw] = rows; log(f"    {kw} 월간 {len(rows)}행")
                    else:
                        m_by_kw[kw] = prev_m.get(kw, []); log(f"    {kw} 월간 실패 → 기존 {len(m_by_kw[kw])}행 유지")
                if do_week:
                    # 주간: 상대지수만 (KR은 시작주+1=월요일)
                    w = wdata.get(qterm, [])
                    if geo == "KR":
                        wr = [[d.isoformat(), kw, v, (d + datetime.timedelta(days=1)).isoformat()] for d, v in w]
                    else:
                        wr = [[d.isoformat(), kw, v] for d, v in w]
                    if wr:
                        w_by_kw[kw] = wr; log(f"    {kw} 주간 {len(wr)}행")
                    else:
                        w_by_kw[kw] = prev_w.get(kw, []); log(f"    {kw} 주간 실패 → 기존 {len(w_by_kw[kw])}행 유지")

        # 키워드 순서대로 누적해서 탭마다 한 번에 기록
        all_m = [row for kw, _, _ in items for row in m_by_kw.get(kw, [])]
        all_w = [row for kw, _, _ in items for row in w_by_kw.get(kw, [])]
        if all_m:
            write_tab(ws_m, hdr_m, all_m); log(f"  → {prefix}_월간 {len(all_m)}행")
        if all_w:
            write_tab(ws_w, hdr_w, all_w); log(f"  → {prefix}_주간 {len(all_w)}행")

    if do_month:
        apply_calc_formulas(sh)   # 계산값을 설정N 참조 수식으로 (자동 반영)
    log("완료")

if __name__ == "__main__":
    sys.exit(main())
