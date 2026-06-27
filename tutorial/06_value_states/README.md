# 06. Value States

이 예제는 값이 없는 셀과 특수 상태를 구분하는 방법을 보여준다.

## 상태

- `<<ABSENT>>`: 값이 없음
- `<<NULL>>`: 의미 있는 null
- `<<EMPTY>>`: 길이 0 문자열
- `<<WS:n>>`: 공백 `n`개

예약 토큰 자체를 문자값으로 쓰고 싶으면 앞에 `\`를 붙인다.

## 파일

- `sample_value_states.tame`

## 태그 정책

- `REQUIRED`: `ABSENT` 불가
- `NULLABLE`: `NULL` 허용
- `EMPTY_OK`: `EMPTY` 허용
- `WS_OK`: whitespace-only 문자열 허용

## 실행 예

상태 확인:

```bash
tametools states tutorial/06_value_states/sample_value_states.tame
```

검증:

```bash
tametools validate tutorial/06_value_states/sample_value_states.tame
```

요약:

```bash
tametools describe tutorial/06_value_states/sample_value_states.tame
```
