# init: 컬럼명과 전체 값에 따른 SEX·AGE 검토

`tametools init`에서 SEX·AGE 후보 열의 **전체 행**을 검사하고, 대화형 선택으로 성별 정규화·출처별 매핑·연령군 생성을 바로 수행한다. 수정한 저장소 소스는 0.4.0이다.

## 바로 실행하기

```sh
tametools init input.csv --output reviewed.tame --interactive
tametools describe reviewed.tame
```

일반 터미널에서는 `--interactive`를 생략해도 질문한다. 파이프·배치에서는 자동으로 질문하지 않는다. 배치 실행이나 다른 프로그램에서 호출할 때는 다음과 같이 설정 파일을 재사용한다.

```sh
tametools init input.csv --output replay.tame --no-interactive \
  --definitions reviewed.init-decisions.toml --require-reviewed
```

`--require-reviewed`는 필수 미정의 항목이 남으면 파일과 보고서를 기록한 뒤 종료 코드 2를 반환한다. 옵션을 생략하면 미정의 값을 유지한 시작 파일을 생성한다. 이때 종료 코드 0은 임상적 검토가 끝났다는 의미가 아니다. API `init_from_table(...)`는 기본적으로 비대화형이며 `interactive=True`로 질문할 수 있다.

## SEX

1. SEX, Sex, patientSex, gender, 성별 등의 열 이름에서 SEX를 추정한다. 전체 행의 상태와 값별 빈도, 정규화 가능한 표기, 매핑이 필요한 값을 표시한다. 숫자 코드뿐 아니라 미인식 문자도 검토 대상이다.
2. 실제 성별 열인지 확인한다. 아니면 STR로 바꾸어 추정을 수정한다.
3. 바로 정규화할지 선택한다. M/F, Male/Female, 남/여 등은 내장 사전으로 인식한다. 0/1/2/9 등의 숫자는 의미를 추측하지 않으며 코드북에 근거해 male/female/other/unknown 중 하나를 지정한다. `skip`은 미정의 상태로 유지한다.
4. `source` 등의 출처 열이 있으면 출처별로 묻는다. 다른 이름의 출처 열도 직접 지정할 수 있다. 예를 들어 Kenya의 1=female, NHANES의 1=male을 같은 SEX 열에 적용할 수 있다. 출처가 없거나 미등록 출처인 행에 다른 출처의 숫자 매핑을 적용하지 않는다.
5. 선택한 매핑을 즉시 적용하고 원래 값은 `SEX__raw`(원 열 이름이 Sex이면 `Sex__raw`)에 보존한다. 매핑하지 못한 값은 삭제하거나 unknown으로 바꾸지 않고 그대로 유지하며 검토 항목으로 남긴다.

출처 정보를 이미 잃은 자료에서는 같은 숫자의 서로 다른 의미를 복구할 수 없다. 코드북 확인은 사용자가 해야 한다. `unknown`은 사용자가 명시적으로 선택한 의미이며 `skip`과 다르다.

비대화형 정의 예:

```toml
[COLUMN.SEX]
TAGS = ["SEX", "CATEGORY", "SEX_CODES"]

[CATEGORIES.SEX_CODES]
VALUES = ["male", "female", "other", "unknown"]
STRICT = true
SOURCE_COLUMN = "source"

[CATEGORIES.SEX_CODES.SOURCE_MAPS.kenya]
"0" = "male"
"1" = "female"

[CATEGORIES.SEX_CODES.SOURCE_MAPS.nhanes]
"1" = "male"
"2" = "female"

[INIT_OPTIONS.SEX.SEX]
NORMALIZE = true
```

기존의 `CATEGORIES` 정의만 주면 코드는 유지된다. init 단계에서 즉시 변환하려면 `NORMALIZE=true`를 선언한다. 대소문자·앞뒤 공백을 제거했을 때 같은 코드에 서로 다른 의미가 선언되면 오류로 중단한다.

## AGE와 AGE_5·AGE_10

AGE, Age_with_unit, PatientAge, 나이, 연령 등의 후보 열은 전체 값을 연령 파서로 검사한다. 인식·실패 건수, 해석한 단위와 연 단위 최소·최대값을 표시한다. JSON 보고서에는 모든 고유값별 빈도와 실패 행 번호 예시(최대 10개)를 남긴다. 앞부분만 표본 추출하는 검사가 아니다.

- 실제 개별 연령 열인지 확인한다. `18-29`처럼 이미 범주화한 자료를 AGE로 확정하지 않고 태그를 수정할 수 있다. AGE_5, AGE_10, age_group, AgeBand, 연령군은 처음부터 AGE_GROUP 범주로 구분한다.
- 단위 없는 숫자는 기본적으로 년(a)이다. 실제로 월(mo) 또는 일(d)이면 즉시 선택할 수 있다. 예를 들어 단위 mo를 선택한 숫자 6은 `6mo`로 기록하고 원 숫자는 `AGE__raw`로 보존한다. 이미 `6mo`, `365.25d`처럼 단위가 명시된 값은 그 단위를 유지한다.
- 연령군은 생성하지 않음 / 5년 / 10년 / 둘 다 중에서 선택한다. AGE 열이 하나이면 AGE_5·AGE_10을 생성하고, 여러 개면 각 원 열 이름을 접두어로 사용한다. 개별 AGE 열은 유지한다.
- 기존 연령 구간 함수와 같은 규칙을 사용한다. 5년군은 `<1`, `1-4`, `5-9`, …, `65-69`, `70+`; 10년군은 `<1`, `1-9`, `10-19`, …, `60-69`, `70+`이다. `<1`은 [0,1), `L-U`는 [L,U+1), `70+`는 [70,∞)를 뜻한다. 따라서 4.999년은 1-4, 5년은 5-9이다. 월은 12로, 일은 365.25로 나누어 연 단위로 해석한다.
- 잘못된 연령이 있으면 기본적으로 그룹 생성을 중단한다. 대화형에서 동의하거나 `INVALID_POLICY="NULL"`로 명시한 경우에만 해당 연령군을 NULL로 남긴다. 원래 잘못된 값과 검토 경고는 유지한다. 기존 ABSENT·NULL·EMPTY·WS 상태는 파생 열에서도 보존한다.
- 생성한 열에는 BY·AGE_GROUP·CATEGORY 태그와 원 열, 구간 폭·경계 규칙, 오류 처리 정책을 기록한다. 동일한 출력 열이 이미 있으면 덮어쓰지 않고 오류를 반환한다. 정의 파일의 PREFIX로 다른 이름을 지정할 수 있다.

```toml
[INIT_OPTIONS.AGE.AGE]
UNIT = "a"

[INIT_OPTIONS.AGE_GROUPS.AGE]
WIDTHS = [5, 10]
PREFIX = "AGE"
OPEN_UPPER = 70
INVALID_POLICY = "ERROR"
```

OPEN_UPPER는 1보다 크고 선택한 구간 폭으로 나누어지는 정수로 변경할 수 있다. 이 검사는 연령 형식과 단위 변환을 확인한다. 원자료의 만 나이·반올림·상한 코딩 또는 임상적 타당성을 자동 확정하지 않는다. 선택한 연령군만으로 임상적 partition이 검증되는 것도 아니다.

## 함께 생성되는 파일과 기록

| 파일 | 용도 |
|---|---|
| reviewed.tame | 선택한 태그·매핑·변환과 보존 원값, META/LOG |
| reviewed.init-review.json | 입력과 처리 후의 전체 값 검토, 미정의 항목, 변환 건수 |
| reviewed.init-decisions.toml | 이번에 실제 선택한 정의와 실행 옵션. 같은 원 입력에 재실행 가능 |
| reviewed.definitions.toml | 추가 검토용 템플릿. DEFINE_ 자리표시자를 확인해야 함 |

자동 기본 CRR=VALUE를 사용자가 검토한 설정으로 바꾸어 기록하지 않는다. 명시적으로 선언한 SETTINGS만 재실행 정의에 넣는다. 원본 입력·외부 정의·저장한 선택 파일의 SHA-256, 즉시 적용한 옵션과 변환별 건수를 INIT 로그에 기록한다. 기존 정의 템플릿은 보존하고, 선택 파일 내용이 달라지면 해시가 붙은 별도 이름으로 저장한다.

