# tametools Web Workbench

이 웹앱은 `tametools`를 FastAPI 백엔드와 SvelteKit 프론트엔드로 나눈 작업용 UI다.

개인 컴퓨터에서 tametools exe 없이 Chrome으로 실행하려면 [개인용 브라우저 배포 안내](../README_BROWSER_KO.md)를 참고한다.
`Start_Chrome.cmd`(Windows) 또는 `bash Start_Chrome.sh`(macOS/Linux)가 별도 Python 환경을 준비하고 Chrome을 연다.

기본 화면은 공통 작업실이다. **공개 예제 불러오기**에서 NHANES 또는 Kenya 자료를 선택하고, 보유한 Excel/TAME 파일과 같은 기능을 시험한다.
[웹 사용 안내](../docs/WEB_QUICKSTART_KO.md)에 검증 순서와 재현 방법이 있다.

## 구조

- `web/backend`: FastAPI API 서버
- `web/frontend`: SvelteKit + AG Grid 프론트엔드
- `web/backend/app/tametools_bridge.py`: `tametools` 변환, 검증, EDA, 익명화 호출 계층

## 실행

비개발자/검사실 사용자용 권장 실행:

Windows에서는 아래 파일을 더블클릭한다.

```text
scripts\start_tametools_web.bat
```

Linux, macOS, WSL에서는 저장소 루트에서 아래 한 줄을 실행한다.

```bash
./scripts/start_tametools_web.sh
```

두 스크립트는 `tametools[report,web,analysis,nhanes]` 설치, 프론트엔드 production build 확인, 백엔드 기동,
브라우저 열기를 한 번에 수행한다. 브라우저 주소는 기본값 `http://127.0.0.1:8765`이다.
`web/frontend/build`가 이미 포함된 배포본에서는 운영 중 Node.js/Vite dev server를 실행하지 않는다.

개발자용 수동 실행:

프론트엔드 production build:

```bash
cd web/frontend
npm install
VITE_TAMETOOLS_API_BASE="" npm run build
```

백엔드:

```bash
python3 -m pip install './tametools[report,web,analysis,nhanes]' --no-build-isolation
cd web/backend
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload
```

브라우저에서 `http://127.0.0.1:8765`을 연다.

개발 중 Vite dev server를 따로 띄우려면 저장소 루트에서 `TAMETOOLS_DEV_FRONTEND=1 ./scripts/start_tametools_web.sh`를 사용한다.

## 검사실 사용자 빠른 시작

1. `scripts\start_tametools_web.bat` 또는 `./scripts/start_tametools_web.sh`를 실행한다.
2. 브라우저에서 `파일 열기`를 누르고 `tutorial/14_capability_probe/clinical_chem.tame`를 연다.
3. 좌측 `기본 작업`에서 `1. 데이터 상태 확인`을 누른다.
4. `2. 입력 오류 검증`으로 태그 기반 오류를 확인한다.
5. `6. 탐색 분석 실행`, `7. EP28 참고구간`, `8. 개인정보 비식별화` 중 필요한 업무를 누른다.
6. 결과는 좌측 `작업 이력`에 새 데이터로 추가된다.
7. `TAME 저장` 또는 `Excel 저장`으로 결과를 저장한다.

## 기능

- `.xlsx`, `.xlsm`, `.tame` 열기
- `DATA`와 `META` 시트가 있는 `.xlsx`는 TAME workbook으로 보고 바로 데이터/메타 탭에 로드
- Excel workbook의 시트별 행/열 수, control sheet, 빈 헤더, 중복 헤더, 수식 셀 검토
- 단일 시트를 DATA로 변환
- 여러 시트를 `SHEET::STR` 태그 provenance 컬럼이 있는 하나의 DATA로 병합. 기본 컬럼명은 `시트명`이며 실제 컬럼명은 `META[MULTISHEET].SOURCE_COLUMN_NAME`에 기록
- AG Grid로 데이터 값을 스프레드시트처럼 탐색
- Meta 탭에서 TOML 메타데이터 편집
- 컬럼 패널에서 컬럼명, 태그, `META[COLUMN]` 추가정보를 함께 편집
- `ID(patient)`, `ID(sample)`, `ID(hospital)` 같은 qualifier 태그를 버튼으로 추가
- 컬럼별 `LABEL`, `DESCRIPTION`, `UNIT`, `UCUM_UNIT`, `LOINC`, `LOCAL_CODE`, `PHI` 편집
- wide RESULT 컬럼별 `PIVOT_CONTEXT.TESTNAME/UNIT/REF_LOW/REF_HIGH` 편집
- `TAG_DEFINITIONS` 패널에서 `AGE5`처럼 기존 태그를 상속하는 사용자 정의 태그 생성
- 데이터셋 요약에서 다중 RESULT, qualifier ID, `PIVOT_CONTEXT`, custom tag 상태 확인
- Validate 탭에서 태그별 원본값 분포 검토와 표준값 변환
- AGE 검토 분포는 `TAG_DEFINITIONS`의 `AGE_BIN_WIDTH`를 반영
- `SEX` 태그 값의 `M/F/남/여` 표준화와 확인된 `1/0` 코딩 변환
- 상태 확인, 검증, 표준화, 저장, 태그를 META/헤더로 이동 실행
- ACTION, ACTION_PIPELINES, ANALYSES, WEB_PLUGINS를 읽어 실행 버튼 자동 생성
- 플러그인 선택 시 RESULT role contract와 다중 RESULT 처리 방식 표시
- 태그 기반 EDA 실행
- `ID`, `ID(patient)`, `ID(hospital)`, `PATIENT_ID`, `HOSPITAL_ID`, `NAME` 태그 기반 익명화
- 새 데이터를 만드는 작업은 원본을 덮어쓰지 않고 좌측 `작업 이력`에 새 `.tame` 노드로 추가
- 생성된 `.tame`의 Meta에는 `[[LOG]]` 이력이 누적
- 현재 workspace를 `.tame` 또는 `.xlsx`로 저장

## 최근 태그/META 기능 확인

1. `tutorial/17_tag_spec_extension/custom_tags_age5.tame`를 연다.
2. 우측 `TAG_DEFINITIONS`에서 `AGE5`가 `AGE`를 상속하고 `AGE_BIN_WIDTH = 5`를 가진 것을 확인한다.
3. `상태 확인`을 누르면 검증 탭의 AGE 분포가 5년 단위로 표시된다.
4. `tutorial/17_tag_spec_extension/wide_pivot_context.tame`를 연다.
5. `AST`, `ALT` 컬럼을 선택하고 `열 태그와 META`의 `PIVOT_CONTEXT` 참고치를 확인한다.
6. `플러그인 실행`에서 `ABNORMAL_FLAG`를 실행하면 AST/ALT가 각각의 참고치로 판정된다.
