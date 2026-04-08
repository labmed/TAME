# TAME Format Proposal

## 방향 재정의

`학술연구과제 결과보고서-tametools_v4.pdf`를 기준으로 보면, `tame`의 핵심은 "스키마 중심 포맷"이 아니라 "헤더 태그 중심 분석 포맷"이다.

즉 사용자는 일반 엑셀 파일을 준비한 뒤, 헤더에 태그만 붙여서 각 컬럼의 의미와 자료형을 선언하고, `tametools` 및 분석 플러그인은 컬럼명 자체가 아니라 태그를 기준으로 동작해야 한다.

예를 들어:

- `[[RESULT::NUM]]보고값` : 이 컬럼은 결과값이며 숫자형으로 처리
- `[[RESULT::<NUM>]]보고값` : 이 컬럼은 결과값이며 부등호 포함 숫자형으로 처리
- `[[AGE]]나이` : 이 컬럼은 연령 컬럼으로 처리
- `[[GENDER]]성별` : 이 컬럼은 성별 컬럼으로 처리
- `[[BY::AGE]]연령대` : 이 컬럼은 그룹 분석용 연령대 컬럼으로 처리

이 설계의 목표는 다음과 같다.

1. 컬럼명이 `보고값`, `결과`, `value`, `result`처럼 달라도 분석이 동일하게 동작한다.
2. `NUM`, `<NUM>`, `STR`, `DATE` 같은 태그로 자료형을 강제해 추후 분석 오류를 줄인다.
3. 참고치 분석, 탐색적 데이터 분석, wide/long 변환 같은 기능이 태그만 보고 자동으로 작동한다.
4. 사용자는 엑셀 헤더 수정만으로 새 플러그인을 사용할 수 있다.

## PDF에서 확인된 핵심 의도

보고서의 방향은 아래처럼 해석하는 것이 맞다.

- `tame`는 기본적으로 `<META>` + `<DATA>`로 이루어진 텍스트 기반 포맷이다.
- 데이터는 TSV로 저장하고 메타는 TOML로 저장한다.
- 엑셀과의 상호 변환이 매우 중요하다.
- 컬럼 태그는 헤더 또는 `<META>`의 `[TAGS]`를 통해 부여한다.
- `tametools`는 태그를 보고 자료형 검증, 전처리, 분석을 수행한다.
- 분석 파이프라인은 `<META>`에 정의되고, 새 함수는 동일한 인터페이스로 추가된다.

따라서 새 제안도 이 방향을 유지해야 한다.

## 핵심 설계 원칙

1. `tame`의 1차 인터페이스는 "태그가 붙은 헤더"다.
2. 컬럼명은 사람을 위한 이름이고, 태그는 기계를 위한 의미다.
3. 플러그인은 컬럼명을 하드코딩하지 않고 태그로 컬럼을 찾는다.
4. 자료형 검증은 데이터 샘플 추정이 아니라 태그 선언을 우선한다.
5. `META`는 실행과 설정의 중심이고, `SCHEMA`와 `JOB`은 선택적 보조 계층이다.
6. 일반 사용자는 엑셀과 `<META>`만으로 사용할 수 있어야 한다.
7. 고급 사용자는 `<SCHEMA>`, `<JOB>`를 분리하거나 내장해서 재사용할 수 있어야 한다.

## 파일 구조

일상적인 사용의 최소 구조는 기존과 같이 `<META>` + `<DATA>`다.

```text
<META>
# TOML metadata
</META>
<DATA>
# TSV data
</DATA>
```

고급 사용이나 배포용 패키징에서는 `<SCHEMA>`와 `<JOB>`를 함께 포함할 수 있다.

```text
<SCHEMA>
# optional TOML schema
</SCHEMA>
<JOB>
# optional TOML jobs
</JOB>
<META>
# TOML metadata
</META>
<DATA>
# TSV data
</DATA>
```

작성 규칙:

- 인코딩은 UTF-8
- 줄바꿈은 LF
- `DATA`는 TSV
- canonical section order는 `<SCHEMA>`, `<JOB>`, `<META>`, `<DATA>`
- 최소 필수 섹션은 `<META>`와 `<DATA>`
- `<SCHEMA>`와 `<JOB>`는 optional

## 엑셀과의 대응

엑셀 호환성은 설계의 중심이어야 한다.

- `DATA` -> `DATA` 시트
- `META` -> `META` 시트
- `SCHEMA` -> optional `SCHEMA` 시트
- `JOB` -> optional `JOB` 시트

실무 흐름은 아래처럼 두 가지를 지원하는 것이 좋다.

1. 일반 엑셀 파일에서 `DATA` 시트 헤더에 태그를 붙이고 `META` 시트만 작성해서 실행
2. `.tame` 파일에서 다시 엑셀로 내보내 수정 후 재실행

즉 사용자는 "엑셀 파일 수정만으로 tametools 분석 기능을 사용하는" 경험을 가져야 한다.

## 태그 시스템

### 1. 기본 문법

헤더 태그의 canonical 문법은 아래로 둔다.

```text
[[TAG]]컬럼명
[[TAG::TAG]]컬럼명
[[TAG::TAG::TAG]]컬럼명
```

예:

```text
[[RESULT::NUM]]보고값
[[RESULT::<NUM>]]보고값
[[AGE]]나이
[[GENDER]]성별
[[BY::AGE]]연령대
[[ITEM]]검사항목명
[[ID]]등록번호
```

규칙:

- 태그는 대문자로 정규화
- 태그는 `::`로 구분
- 태그 블록은 `[[`로 시작하고 `]]`로 끝난다
- `]]` 뒤 문자열 전체는 표시용 컬럼명이다
- 태그 내부에서는 `_`를 사용할 수 있다
- 표시용 컬럼명은 한글/영문 자유
- 플러그인은 표시용 컬럼명이 아니라 태그를 본다
- 기존 `_TAG_컬럼명` 문법은 읽기 호환만 유지하고, 새로 저장할 때는 `[[...]]`를 canonical form으로 사용한다

### 2. 태그 출처

컬럼 태그는 세 곳에서 올 수 있다.

1. 헤더 태그
2. `<META>`의 `[TAGS]`
3. `<SCHEMA>`의 기본 태그 선언

권장 우선순위:

1. 헤더 태그
2. `[TAGS]`
3. `<SCHEMA>`

즉 엑셀 헤더에 직접 붙인 태그가 가장 강하다.

### 3. META의 `[TAGS]`

헤더를 바꾸기 어렵거나 추가 태그를 덧붙이고 싶을 때 `[TAGS]`를 사용한다.

```toml
[TAGS]
"보고값" = ["RESULT", "NUM"]
"성별" = ["GENDER", "BY"]
"검사항목명" = ["ITEM"]
```

헤더와 `[TAGS]`에 모두 존재하면 합집합으로 처리하되, 충돌 시 헤더를 우선한다.

## 권장 태그 분류

### 1. 의미 태그

- `RESULT` : 결과값 컬럼
- `ITEM` : 검사명 또는 검사코드 컬럼
- `ID` : 등록번호, 검체번호, 방문번호 같은 식별 컬럼
- `AGE` : 연령 컬럼
- `GENDER` : 성별 컬럼
- `BY` : 그룹 분석에 사용할 컬럼
- `DATE` : 날짜 컬럼
- `DATETIME` : 날짜시간 컬럼
- `UNIT` : 단위 컬럼
- `REF_LOW` : 참고치 하한
- `REF_HIGH` : 참고치 상한
- `TEXT` : 텍스트 결과 성격
- `COMMENT` : 비고

### 2. 자료형 태그

- `NUM` : 순수 숫자
- `<NUM>` : 부등호 포함 숫자
- `STR` : 문자열
- `TXT` : 긴 자유 텍스트
- `CAT` : 범주형 문자열
- `IMG` : base64 이미지
- `B` : base64 바이너리
- `DATE(%y%m%d)` : 날짜 포맷 지정
- `DATETIME` : 날짜시간

### 3. 조합 예시

- `[[RESULT::NUM]]결과`
- `[[RESULT::<NUM>]]결과`
- `[[BY::GENDER]]성별`
- `[[BY::AGE]]연령대`
- `[[ID::STR]]등록번호`
- `[[ITEM::CAT]]검사항목명`

## 태그 해석 규칙

### 1. RESULT

`RESULT`는 이 컬럼이 분석 대상 결과값이라는 뜻이다.

- `RESULT + NUM` : 수치 결과
- `RESULT + <NUM>` : 부등호 포함 수치 결과
- `RESULT + TXT` : 텍스트 결과
- `RESULT + CAT` : 반정량 또는 범주 결과

### 2. NUM

`NUM`은 순수 숫자만 허용한다.

- 허용 예: `3`, `3.2`, `-0.5`
- 비허용 예: `<3`, `>500`, `positive`

검증 실패 시 동작은 `<META>`의 설정을 따른다.

### 3. <NUM>

`<NUM>`은 부등호 포함 숫자를 허용한다.

예:

- `<3`
- `>500`
- `<=0.2`
- `>=1200`
- `3.4`

이 태그가 있으면 파서는 내부적으로 아래처럼 해석할 수 있어야 한다.

- 원본 텍스트
- 비교연산자
- 숫자값

분석 시 처리 방법은 `<META>`의 설정을 따른다.

### 4. AGE

`AGE`는 연령 컬럼이다.

보고서의 의도를 따라 다음 입력을 지원하는 것이 맞다.

- `10` : 10세
- `2m` : 2개월
- `1d` : 1일

`tametools`는 이를 정규화해 분석용 나이 단위를 계산할 수 있어야 한다.

### 5. GENDER

`GENDER`는 성별 컬럼이다.

아래 값들을 표준화할 수 있어야 한다.

- `M/F`
- `남/여`
- `Male/Female`
- 기타 기관별 코드

### 6. BY

`BY`는 그룹 분석용 태그다.

예:

- `[[BY::AGE]]연령대`
- `[[BY::GENDER]]성별`
- `[[BY]]진료과`

`DESCRIBE`, 참고치 분석, 비교 분석 플러그인은 `BY` 태그를 가진 컬럼을 우선적으로 그룹 변수로 사용할 수 있다.

## 플러그인 계약

이 부분이 이번 제안의 핵심이다.

플러그인은 컬럼명을 찾지 않는다. 태그를 찾는다.

### 1. DESCRIBE / EDA

기본 EDA 플러그인은 다음과 같이 동작한다.

- `RESULT::NUM` 또는 `NUM` 컬럼은 숫자 요약
- `STR`, `CAT`, `TXT` 컬럼은 범주/텍스트 요약
- `BY` 태그가 있으면 그룹별 요약
- `ITEM` + `RESULT` 구조면 항목별 요약

즉 `보고값`, `결과`, `Value`, `AST`라는 컬럼명이 아니라 태그를 기준으로 요약한다.

### 2. VALIDATE

검증 플러그인은 태그 기반으로 동작한다.

- `NUM`이면 숫자 검증
- `<NUM>`이면 부등호 포함 숫자 검증
- `DATE(%...)`면 날짜 포맷 검증
- `AGE`면 연령 파싱 검증
- `GENDER`면 성별 표준값 검증

검증 정책은 `<META>`의 `[SETTINGS]`에서 정의한다.

### 3. 참고치 분석

참고치 분석 플러그인은 아래 태그를 기준으로 컬럼을 찾는다.

필수:

- `RESULT`

권장:

- `ITEM`

선택:

- `AGE`
- `GENDER`
- `BY`
- `DATE` 또는 `DATETIME`

동작 예:

- `RESULT`만 있으면 전체 참고치 분석
- `RESULT + ITEM`이면 항목별 참고치 분석
- `RESULT + AGE + GENDER`이면 연령/성별 층화 참고치 분석
- `RESULT + BY::AGE`이면 연령대 그룹별 참고치 분석
- `RESULT + BY::GENDER`이면 성별 그룹별 참고치 분석

즉 플러그인은 `성별`이라는 이름을 찾는 것이 아니라 `GENDER` 태그를 찾는다.

### 4. long / wide 변환

태그를 이용하면 wide/long 변환 기준도 일관되게 잡을 수 있다.

- `ITEM + RESULT`가 있으면 long form으로 해석
- 여러 개의 `RESULT` 컬럼이 있고 `ITEM`이 없으면 wide form으로 해석
- `ID` 태그가 key 후보가 된다
- `BY` 태그는 그룹 분할의 기준이 된다

## META 구조

`META`는 여전히 tametools 실행의 중심이다.

보고서의 방향에 맞춰 아래 구성을 유지하는 것이 좋다.

```toml
[INFO]
DESCRIPTION = """임상화학 분석용 데이터"""
AUTHOR = """대한임상화학회 데이터분석 위원회"""

[USE]
DEFAULT = ["KSCC"]

[TAGS]
"보고값" = ["RESULT", "NUM"]
"성별" = ["GENDER", "BY"]
"나이" = ["AGE"]

[SETTINGS]
VALIDATE_ERROR = "DELETE"
CRR = "VALUE"

[WORKS]
DEFAULT = ["IMPORT", "VALIDATE", "DESCRIBE"]
RI = ["IMPORT", "VALIDATE", "REFERENCE_INTERVAL"]
```

### 권장 SETTINGS

- `VALIDATE_ERROR = "DELETE" | "REPORT" | "STOP"`
- `CRR = "DELETE" | "VALUE" | "KEEP"`

`CRR`는 `<NUM>` 결과 처리 규칙이다.

- `DELETE` : 부등호 포함 결과 제외
- `VALUE` : 숫자만 추출해 사용
- `KEEP` : 텍스트 유지, 숫자 분석에서는 제외

보고서 예시를 보면 `CRR=VALUE`가 중요한 실무 옵션이다.

## SCHEMA의 역할

`SCHEMA`는 필수가 아니다. 일반 사용자는 없어도 된다.

`SCHEMA`는 아래 경우에만 쓰는 것이 맞다.

- 헤더 태그와 `[TAGS]`를 추출해 재사용할 때
- 기관 간 공통 태그 규약을 배포할 때
- 플러그인별 필수 태그 조합을 문서화할 때
- 고급 검증 규칙을 공유할 때

즉 `SCHEMA`는 "헤더 태그를 대체하는 계층"이 아니라 "헤더 태그를 정리하고 재사용하는 보조 계층"이어야 한다.

예시:

```toml
[schema]
id = "clinical-chemistry-basic"
tag_mode = "header-first"

[required_tags.describe]
any = ["NUM", "STR", "CAT", "TXT"]

[required_tags.reference_interval]
all = ["RESULT"]
recommended = ["ITEM", "AGE", "GENDER"]

[column."보고값"]
tags = ["RESULT", "<NUM>"]

[column."성별"]
tags = ["GENDER", "BY"]
```

## JOB의 역할

`JOB`도 필수가 아니다.

`JOB`은 `<META>`의 `[WORKS]`, `[SUBWORKS]`, 함수 옵션을 추출해서 별도 파일로 재사용할 때 쓴다.

즉 일상적 사용은 `<META>`만으로 충분하고, `JOB`은 파이프라인 배포와 버전관리용 보조 계층이다.

예시:

```toml
[jobs]
default = "ri-basic"

[[job]]
id = "ri-basic"

[[job.step]]
action = "import_xlsx"

[[job.step]]
action = "validate"

[[job.step]]
action = "reference_interval"

[[job.step]]
action = "export_xlsx"
```

## sidecar 추출 규칙

하나의 `.tame` 파일에 포함된 내용을 필요하면 밖으로 뺄 수 있어야 한다.

- `<META>` -> `name.tame.meta`
- `<DATA>` -> `name.tame.data`
- `<SCHEMA>` -> `name.schema.toml`
- `<JOB>` -> `name.jobs.toml`

반대로 sidecar를 다시 포함해 하나의 `.tame`로 패키징할 수도 있어야 한다.

권장 우선순위:

1. 헤더 태그
2. `<META>`의 `[TAGS]`
3. `<SCHEMA>`

즉 sidecar가 있더라도 엑셀 헤더 태그를 가장 우선한다.

## 권장 예시

```text
<META>
[INFO]
DESCRIPTION = """태그 기반 임상화학 결과 데이터"""

[TAGS]
"등록번호" = ["ID", "STR"]

[SETTINGS]
VALIDATE_ERROR = "DELETE"
CRR = "VALUE"

[WORKS]
DEFAULT = ["VALIDATE", "DESCRIBE"]
RI = ["VALIDATE", "REFERENCE_INTERVAL"]
</META>
<DATA>
[[ID::STR]]등록번호	[[GENDER]]성별	[[AGE]]나이	[[ITEM]]검사항목명	[[RESULT::<NUM>]]보고값	[[BY]]진료과
A0001	F	32	AST	25	IM
A0002	M	67	AST	<3	GS
A0003	F	28	ALT	31	IM
</DATA>
```

이 경우 `tametools`는 아래처럼 해석한다.

- `등록번호`는 식별용 문자열
- `성별`은 성별 변수
- `나이`는 연령 변수
- `검사항목명`은 long form item 변수
- `보고값`은 부등호 허용 결과값
- `진료과`는 그룹 분석 변수

따라서:

- `DESCRIBE`는 결과값을 숫자형으로 요약
- `VALIDATE`는 `<NUM>` 규칙으로 결과 검증
- 참고치 분석은 `ITEM` 기준 항목별 분석
- `AGE`, `GENDER`, `BY`가 있으면 층화/그룹 분석 자동 시행

## 결론

새 제안의 중심은 `<SCHEMA>`나 `<JOB>`가 아니다. 중심은 "헤더 태그를 붙인 엑셀/테이블"이다.

`tametools`는 이 태그를 기준으로 컬럼 의미와 자료형을 해석하고, 플러그인은 컬럼명이 아니라 태그를 기준으로 분석을 수행해야 한다. `<SCHEMA>`와 `<JOB>`는 이 태그 기반 구조를 기관 간 재사용하거나 추출·포함하기 위한 선택적 계층으로 두는 것이 맞다.

즉 최종 방향은 아래 한 줄로 정리된다.

`컬럼명은 사람이 읽고, 태그는 tametools가 읽는다.`
