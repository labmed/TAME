# 04. Merge Multi-Hospital

이 예제는 병원별 tame 파일을 합치는 방법을 보여준다.

## 파일

- `hospital_a.tame`
- `hospital_b.tame`

## 상황

- 병원 A와 병원 B는 같은 의미의 컬럼을 서로 다른 이름으로 저장한다
- 예: `성별` vs `gender`, `나이` vs `ageYears`, `보고값` vs `resultValue`
- 태그는 동일하므로 `tametools`는 `GENDER`, `AGE`, `RESULT` 의미로 병합한다
- 병원 A는 `NUM`, 병원 B는 `<NUM>`을 쓰므로 `NUM/<NUM>` 충돌 정책도 정해야 한다

## 실행 예

`promote`:

```bash
python3 -m tametools merge \
  tutorial/04_merge_multihospital/hospital_a.tame \
  tutorial/04_merge_multihospital/hospital_b.tame \
  --output tutorial/04_merge_multihospital/merged_promote.tame \
  --num-conflict promote
```

`split`:

```bash
python3 -m tametools merge \
  tutorial/04_merge_multihospital/hospital_a.tame \
  tutorial/04_merge_multihospital/hospital_b.tame \
  --output tutorial/04_merge_multihospital/merged_split.tame \
  --num-conflict split
```

`harmonize`:

```bash
python3 -m tametools merge \
  tutorial/04_merge_multihospital/hospital_a.tame \
  tutorial/04_merge_multihospital/hospital_b.tame \
  --output tutorial/04_merge_multihospital/merged_harmonize.tame \
  --num-conflict harmonize
```

병합 후 컬럼 확인:

```bash
python3 -m tametools columns tutorial/04_merge_multihospital/merged_promote.tame
```

## 정책 설명

- `strict`: 충돌이 있으면 중단
- `promote`: `NUM`을 `<NUM>`로 승격
- `split`: `보고값__cmp`, `보고값__num`으로 분리
- `harmonize`: `<NUM>`로 승격한 뒤 threshold도 통일

## 기대 결과

- 컬럼명이 서로 달라도 `GENDER`, `AGE`, `RESULT`, `TESTNAME` 같은 태그 표준명으로 합쳐진다
- `source_dataset` 컬럼으로 원본 병원을 추적할 수 있다
- 이후 참고치 계산, EDA, merge 후 분석이 태그 기준으로 그대로 동작한다

## 후속 분석

merge 후 EDA:

```bash
python3 -m tametools eda tutorial/04_merge_multihospital/merged_harmonize.tame --comparator-policy HARMONIZE
```
