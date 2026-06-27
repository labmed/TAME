# 14. 기능 커버리지 점검 (실무 예제 20개)

이 폴더는 "현재 tametools로 가능한 예제"가 아니라, **실무에서 자주 하는 작업 20개를 먼저
정의하고** 그것을 tametools로 실행해 본 점검 자료다. 결과와 구현 백로그는
[docs/IMPLEMENTATION_GAPS.md](../../docs/IMPLEMENTATION_GAPS.md)에 정리했다.

> **저장된 재실행 가능한 예제 20개 + 회귀 검증기는 [`examples/`](examples/) 폴더에 있다.**
> `python3 tutorial/14_capability_probe/examples/run_all.py` 로 20개를 한 번에 실행·검증한다
> (전부 통과 시 `20/20 PASS`). 이 검증기는 자동 테스트(`test_capability_probe_20_examples_all_pass`)
> 에도 포함되어, 코드 수정 후 `python3 -m unittest`로도 동작이 확인된다.

## 데이터 생성

```bash
python3 tutorial/14_capability_probe/make_dataset.py
# -> clinical_chem.tame (1,277행 × 16컬럼)
```

환자 반복방문(델타체크), 2개 장비(방법비교), 결측·중복·이상치·dirty 카테고리를 의도적으로
포함한 실무형 임상화학 데이터다.

## 핵심 결과 요약

- 전처리 10개 중 **피벗·분리/결합·문자필터**(P2/P9/P10)는 ACTION으로 실행됨.
  **중복제거·숫자필터·산술/날짜 파생·결측대치·이상치제거·범용 재코딩·키조인**은 미구현.
- 분석 10개 중 **RI·EDA·TAT·워크로드·장비bias·IQR이상치·델타**(C1–C7)는 플러그인으로 실행됨.
  **이상결과 플래그(H/L/N)·양성률·방법비교 회귀**(C8–C10)는 미구현.
- 실행 중 발견: 파라미터형 날짜 태그(`DATE(%Y-%m-%d)`) `[[...]]` 헤더 파싱 버그,
  `run-plugin` 옵션 전달 불가, 컬럼 미감지 시 KeyError 크래시.

## 재현 예 (되는 것)

```bash
F=tutorial/14_capability_probe/clinical_chem.tame
# 분석: TAT, 장비 bias, 델타체크 (META 최상단에 MODE="..." 지정 후)
tametools run-plugin $F CHEMISTRY_ANALYSIS --output out.tame
# 전처리: 결측행 제거 / 컬럼 분리 (META[ACTIONS] 정의 후)
tametools run-action $F DROP_MISSING --output dropped.tame
```

## 안 되는 것 (구현 백로그)

[docs/IMPLEMENTATION_GAPS.md](../../docs/IMPLEMENTATION_GAPS.md)의 Tier 1~4 참조.
대표적으로 숫자비교 필터, 산술 파생컬럼(`{AST}/{ALT}`), 참고치 대비 이상결과 플래그,
중복제거, 키조인, 방법비교 회귀(Passing-Bablok/Deming).
</content>
