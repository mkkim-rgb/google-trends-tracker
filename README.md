# Google Trends Tracker

브랜드×국가별 구글 트렌드(월간/주간)를 수집해 구글시트에 기록. GitHub Actions(공개 레포=무료 무제한)로 자동 실행.

- **월간**: 2023-01~현재, 월 단위, `상대지수 × 쿼리지수(N)` = 계산값
- **주간**: 최근 1년, 주 단위, 상대지수만 (KR은 `시작주+1`=월요일 컬럼 추가)
- 설정탭에서 `브랜드/나라/키워드/쿼리지수` 읽어 동작. 탭은 자동 생성·전체 새로고침.

## 설정탭 예시 (시트에 `설정` 탭 생성)
| 브랜드 | 나라 | 키워드 | 쿼리지수 |
|---|---|---|---|
| UN | KR | 어노브 | 58 |
| UN | US | UNOVE | … |
| UN | JP | ユノブ | … |
| DF | KR | 닥터포헤어 | … |
| DF | US | Dr.FORHAIR | … |

→ 생성 탭: `UN_KR_월간`, `UN_KR_주간`, … (브랜드_나라_월간/주간)

## 셋업 (1회)
1. **공개 GitHub 레포** 생성 → 이 폴더 파일 push
2. **Google Cloud**: 서비스계정 생성 → Google Sheets API + Drive API 사용설정 → JSON 키 다운로드
3. 대상 **구글시트를 서비스계정 이메일에 편집자로 공유**
4. 레포 **Settings → Secrets and variables → Actions** 에 추가:
   - `GOOGLE_SA_KEY` = JSON 키 전체 내용
   - `SHEET_ID` = 시트 URL의 `/d/<여기>/edit`
5. **Actions 탭 → google-trends-collect → Run workflow** 로 수동 테스트

## 스케줄
`.github/workflows/trends.yml` cron = 매주 월요일 06:00 KST. 변경 시 cron 수정.

## 주의
- pytrends는 비공식이라 429(rate limit)·구조변경에 취약. 스크립트에 재시도/간격 내장.
- 월간 N은 월간 정규화 기준. 사상 최고 피크가 새로 찍히면 N 재계산 필요.
- 비밀(키·시트ID)은 GitHub Secrets에만 — 코드/레포에 평문 금지.
