# 16. Step-by-step Hands-on Guide

이 폴더는 발주처가 실제 실행 흐름을 볼 수 있도록 만든 재현 실습 자료다.

## 구성

- `00_raw_lis.data.tame`: 태그 없는 LIS 추출 원자료
- `01_basic_tags.meta.tame`: `[COLUMN]` 태그만 추가한 META sidecar
- `02_actions_and_analyses.meta.tame`: 태그, 전처리 ACTION, 분석 메뉴, 보고서 플러그인 설정
- `00_raw_lis_renamed.data.tame`: 컬럼명이 바뀐 같은 데이터
- `demographics.*.tame`: `ID(patient)` 기반 join용 외부 테이블
- `run_hands_on.py`: 전체 실습 명령을 순서대로 실행하고 로그 생성

## 전체 재현

```bash
python3 tutorial/16_hands_on_guide/run_hands_on.py
```

실행 후 `tutorial/16_hands_on_guide/outputs/execution_log.md`와 여러 결과 `.tame`,
`10_combined_report.docx`가 생성된다.

제출용 설명 문서는 `docs/실습형_핸즈온_가이드_20260624.md`와
`docs/실습형_핸즈온_가이드_20260624.docx`에 있다.
