# 01. Tagged EDA

이 예제는 헤더 태그만으로 `VALIDATE`, `DESCRIBE`, `EDA`가 어떻게 동작하는지 보여준다.

## 파일

- `sample_eda.tame`

## 확인할 점

- `등록번호`는 `[[ID::STR]]등록번호`
- `성별`은 `[[GENDER]]성별`
- `나이`는 `[[AGE]]나이`
- `검사항목명`은 `[[ITEM]]검사항목명`
- `보고값`은 `[[RESULT::<NUM>]]보고값`
- `진료과`는 `[[BY]]진료과`

즉 컬럼명 자체가 아니라 태그가 분석 동작을 결정한다.

## 실행 예

```bash
python3 -m tametools columns tutorial/01_tagged_eda/sample_eda.tame
python3 -m tametools validate tutorial/01_tagged_eda/sample_eda.tame
python3 -m tametools describe tutorial/01_tagged_eda/sample_eda.tame
python3 -m tametools eda tutorial/01_tagged_eda/sample_eda.tame --comparator-policy VALUE
python3 -m tametools run tutorial/01_tagged_eda/sample_eda.tame DEFAULT
```

## 기대 결과

- 잘못된 성별, 나이, `<NUM>` 형식은 `VALIDATE`에서 잡힌다.
- `보고값`은 숫자형 결과로 요약된다.
- `EDA`는 comparator profile과 policy impact를 보여준다.
