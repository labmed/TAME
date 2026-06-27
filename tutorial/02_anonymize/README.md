# 02. Anonymize

이 예제는 `HOSPITAL_ID`, `ID`, `NAME` 태그를 이용한 익명화를 보여준다.

## 파일

- `sample_phi.tame`

## 목표

- `HOSPITAL_ID`, `ID` 태그 컬럼을 안정적으로 해시
- `NAME` 태그 컬럼 제거
- 익명화 결과 파일과 원본↔익명값 매핑 CSV를 함께 저장

## 입력 데이터 확인

```bash
tametools columns tutorial/02_anonymize/sample_phi.tame
```

이 예제의 핵심 태그는 다음과 같다.

- `ID`, `HOSPITAL_ID` 태그 컬럼은 해시
- `NAME` 태그 컬럼은 제거

## 기본 익명화

```bash
tametools anonymize tutorial/02_anonymize/sample_phi.tame \
  --output tutorial/02_anonymize/sample_phi_anon.tame \
  --mapping-output-dir tutorial/02_anonymize/sample_phi_mappings
```

이 명령은 세 가지를 동시에 수행한다.

- 익명화된 데이터셋 저장
- 컬럼별 매핑 테이블 출력
- 매핑 CSV 파일 저장

## 생성 파일 확인

```bash
ls -1 tutorial/02_anonymize/sample_phi_mappings
sed -n '1,10p' tutorial/02_anonymize/sample_phi_mappings/manifest.csv
sed -n '1,10p' tutorial/02_anonymize/sample_phi_mappings/병원ID.mapping.csv
sed -n '1,10p' tutorial/02_anonymize/sample_phi_anon.tame
```

기대 구조:

- `sample_phi_anon.tame`
- `sample_phi_mappings/manifest.csv`
- `sample_phi_mappings/병원ID.mapping.csv`
- `sample_phi_mappings/등록번호.mapping.csv`

`manifest.csv`는 어떤 컬럼이 어떤 파일로 저장되었는지 보여준다.

컬럼을 직접 지정하려면:

```bash
tametools anonymize tutorial/02_anonymize/sample_phi.tame \
  --hash-column 병원ID \
  --hash-column 등록번호 \
  --drop-column 이름 \
  --salt demo \
  --output tutorial/02_anonymize/sample_phi_anon_demo.tame \
  --mapping-output-dir tutorial/02_anonymize/sample_phi_mappings_demo
```

## 결과 해석

- `병원ID`, `등록번호`는 `anon_...` 형식으로 바뀐다.
- `이름` 컬럼은 제거된다.
- 매핑 테이블이 출력된다.
- `--mapping-output-dir`를 주면 `manifest.csv`, `병원ID.mapping.csv`, `등록번호.mapping.csv` 같은 파일이 저장된다.

## 운영 주의점

- 매핑 CSV는 원본 식별정보를 포함하므로 익명화 결과 파일보다 더 엄격하게 관리해야 한다.
- 재현 가능한 해시가 필요하면 `--salt` 값을 고정한다.
- 다른 프로젝트와 분리하고 싶으면 salt를 프로젝트별로 다르게 둔다.
