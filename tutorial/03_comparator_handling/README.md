# 03. Comparator Handling

이 예제는 `<NUM>` 컬럼을 분석하는 방법과 harmonization을 보여준다.

## 파일

- `sample_thresholds.tame`

## 문제 상황

같은 검사(AST)에서 아래 값이 섞여 있다.

- `<30`
- `<20`
- `22`
- `18`

이 경우 단순 numeric 변환만 하면 비교가 어색하다. 그래서 세 가지를 지원한다.

1. `DELETE`: bounded value를 숫자 분석에서 제외
2. `VALUE`: `<30 -> 30`처럼 숫자만 추출
3. `HARMONIZE`: `<20`과 `<30`이 섞이면 `<30`으로 통일하고, `20<=x<30` exact 값도 `<30`으로 변환

## 실행 예

EDA로 현황 확인:

```bash
python3 -m tametools eda tutorial/03_comparator_handling/sample_thresholds.tame --comparator-policy DELETE
python3 -m tametools eda tutorial/03_comparator_handling/sample_thresholds.tame --comparator-policy VALUE
python3 -m tametools eda tutorial/03_comparator_handling/sample_thresholds.tame --comparator-policy HARMONIZE
```

`<NUM>` 분리:

```bash
python3 -m tametools split-comparator tutorial/03_comparator_handling/sample_thresholds.tame \
  --output tutorial/03_comparator_handling/sample_thresholds_split.tame
```

threshold harmonization:

```bash
python3 -m tametools harmonize-comparator tutorial/03_comparator_handling/sample_thresholds.tame \
  --output tutorial/03_comparator_handling/sample_thresholds_harmonized.tame
```

## 기대 결과

- `EDA`의 `comparator_profile`에서 `<30`, `<20`이 따로 보인다.
- `EDA`의 `harmonization_preview`에서 unified threshold가 `30`으로 제안된다.
- harmonization 후 `22`는 `<30`으로 바뀌고 `18`은 그대로 유지된다.
