# Extensive Validation Scenario Set

이 디렉터리는 검증 부족 지적에 대응하기 위한 저장형 예시 세트다. 각 scenario는
`scenario_matrix.py` 안에 목적, 입력 TAME, 실행 작업, 기대 조건을 함께 가진다.
현재 187개 scenario를 제공하며, unittest에서는 scenario별 독립 테스트 187개와
수량 확인 테스트 1개로 실행된다.

실행:

```bash
python3 tutorial/15_extensive_validation/run_extensive_validation.py
python3 -m unittest discover -s tametools/tests -p test_extensive_validation_examples.py
```

구성:

- 값 검증: `NUM`, `<NUM>`, `AGE`, `SEX`, `DATE`, `DATETIME`, cell state
- IO: 중복 헤더, 짧은/긴 행, custom null token, malformed META
- ACTION 성공: 생성, 파생, 날짜/TAT, impute, filter, recode, pivot, unit convert
- ACTION 오류: 사용자 설정 실수를 clean `ActionError`로 처리하는지
- Pipeline: 태그만 사용하는 다단계 전처리
- Plugin: reference interval, chemistry, QC, ROC, trend, abnormal flag, autoverification
- Wide multi-result: `AST`/`ALT` 같은 다중 `RESULT`가 각 플러그인에서 반복 처리되는지

이 예시 세트는 `tametools/tests/test_extensive_validation_examples.py`에서 그대로 실행된다.
