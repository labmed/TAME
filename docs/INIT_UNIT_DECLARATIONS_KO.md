# init 정의에 따른 단위 환산

`init --definitions reviewed.toml`에서 원 단위와 환산 계수를 선언하면 원값을 보존하면서 환산할 수 있습니다. 기본 검토 절차는 [init 사용 안내](INIT_SEMANTICS_KO.md)를 참고하세요.

```toml
[COLUMN.creatinine]
TAGS = ["RESULT", "NUM"]
ID = "crea"
UNIT = "mg/dL"

[INIT_OPTIONS.UNIT_CONVERSIONS.creatinine]
FROM_UNIT = "mg/dL"
TO_UNIT = "umol/L"
FACTOR = "88.4"
REASON = "Reviewed creatinine display-unit conversion"
```

```sh
tametools init original.csv --output converted.tame --no-interactive --definitions reviewed.toml
tametools describe converted.tame
```

COLUMN.UNIT만 선언하면 수치는 변하지 않습니다. 명시한 환산의 FROM_UNIT은 원 COLUMN.UNIT과 일치해야 합니다. 계수는 양의 유한수, OFFSET(선택)은 유한수여야 하며, 지원 단위와 검토한 REASON을 요구합니다. 숫자·부등호는 기존 convert_cell로 처리하고 ABSENT·NULL·EMPTY·WS 상태를 유지합니다. 해석할 수 없는 값을 발견하면 파일을 쓰기 전에 중단합니다.

원값은 입력 해시가 포함된 RAW 열에 남습니다. 원 단위와 출처별 ID는 RAW 열의 메타데이터에, 환산 내용과 원값 열 이름은 INIT.TRANSFORMATIONS와 LOG에 기록합니다. 공통 검사 ID를 병합할 때 출처별 원값 열 이름이 다르다는 이유로 변환된 검사 값이 충돌하지 않도록 했습니다.

같은 원 입력에 저장된 init 선택 파일을 적용하여 재현합니다. `.init-review.json`의 decisions_path는 이번 실행에서 실제 저장한 선택 파일을 가리킵니다. 임의의 계수를 자동 추정하거나 단위 선언만으로 검사법 동등성을 확정하지 않습니다.

