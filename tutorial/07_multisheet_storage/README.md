# 07. Multi-Sheet Storage

이 예제는 서로 다른 시트의 데이터를 하나의 `DATA` 섹션으로 평탄화하고, 다시 여러 엑셀 시트로 복원하는 방법을 보여준다.

## 핵심 규칙

- 여러 엑셀 시트를 읽으면 `tametools`가 `SHEET::STR` 태그가 붙은 provenance 컬럼을 추가한다. 기본 컬럼명은 `시트명`이다.
- 시트마다 없는 컬럼은 `ABSENT`로 채운다.
- 다시 `.xlsx`로 저장할 때는 `SHEET` 태그 컬럼 기준으로 여러 시트로 나눈다.
- 각 시트에서는 해당 시트에서 전부 `ABSENT`인 컬럼을 자동으로 뺀다.

## 예제 파일

- `sample_multisheet_flat.tame`

## 실행 예

단일 `.tame`를 여러 시트 `.xlsx`로 저장:

```bash
tametools info tutorial/07_multisheet_storage/sample_multisheet_flat.tame
tametools columns tutorial/07_multisheet_storage/sample_multisheet_flat.tame
tametools run tutorial/07_multisheet_storage/sample_multisheet_flat.tame DEFAULT --output tutorial/07_multisheet_storage/sample_multisheet_flat.xlsx
```

여러 시트를 다시 하나의 `.tame`로 평탄화:

```bash
tametools info tutorial/07_multisheet_storage/sample_multisheet_flat.xlsx
tametools columns tutorial/07_multisheet_storage/sample_multisheet_flat.xlsx
tametools describe tutorial/07_multisheet_storage/sample_multisheet_flat.xlsx
```

## 기대 결과

- `SHEET` 태그 컬럼으로 원래 시트를 추적할 수 있다.
- 서로 다른 종류의 데이터셋도 하나의 큰 표에 같이 저장할 수 있다.
- 다시 `.xlsx`로 저장하면 `Chemistry`, `Hematology` 같은 시트로 분리된다.
