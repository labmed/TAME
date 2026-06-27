# 11. Command Pipelines

이 예제는 `META[PIPELINES]`에 여러 `tametools` 명령을 저장해 두고 한 번에 실행하는 방법을 보여준다.

## 파일

- `sample_command_pipeline.tame`

## 확인할 점

- `[WORKS]`의 `RI`는 `REFERENCE_INTERVAL` 플러그인을 실행하는 work이다.
- `[PIPELINES]`의 `DEFAULT`는 `sample -> run RI`를 순서대로 실행한다.
- `[PIPELINES]`의 `EXPORT_SQL`은 샘플 추출 뒤 상대 경로 `outputs/ri_sample.sql`로 SQL export를 만든다.
- `run-pipeline`은 같은 데이터셋을 계속 넘기면서 명령 체인을 실행한다.

## 실행 예

파이프라인 목록 확인:

```bash
tametools pipelines tutorial/11_command_pipelines/sample_command_pipeline.tame
```

기본 파이프라인 실행 후 최종 `.tame` 저장:

```bash
tametools run-pipeline tutorial/11_command_pipelines/sample_command_pipeline.tame \
  DEFAULT \
  --output /tmp/pipeline_result.tame
```

결과 확인:

```bash
tametools info /tmp/pipeline_result.tame
tametools columns /tmp/pipeline_result.tame
```

상대 경로 export가 들어간 파이프라인 실행:

```bash
tametools run-pipeline tutorial/11_command_pipelines/sample_command_pipeline.tame EXPORT_SQL
```

생성 파일 확인:

```bash
ls -1 tutorial/11_command_pipelines/outputs
sed -n '1,20p' tutorial/11_command_pipelines/outputs/ri_sample.sql
```

## 기대 결과

- `DEFAULT`는 항목별 1행 샘플을 만든 뒤 `REFERENCE_INTERVAL`을 적용한다.
- 최종 결과에는 `참고치하한`, `참고치상한`, `성별그룹`, `연령그룹` 같은 컬럼이 추가된다.
- `EXPORT_SQL`은 원본 파일 기준 상대 경로에 SQL 스크립트를 저장한다.

## 언제 쓰면 좋은가

- 같은 입력 자료에 대해 반복적으로 `sample -> plugin -> export` 흐름을 돌릴 때
- `.meta.tame`와 함께 배포해서 동일한 분석 절차를 재사용하고 싶을 때
- `WORKS`보다 조금 더 CLI에 가까운 체인을 문서화하고 싶을 때
