# 18. REFERENCE_INTERVAL 플러그인 검증

알려진 분포에서 **샘플링**해 ground truth(참값)를 만들고, 실제 `tametools run-plugin
REFERENCE_INTERVAL` 결과와 대조해 플러그인을 검증한다. 모수적(정규)·비모수적(우편향) 등 다양한
시나리오를 포함한다.

## 재현

```bash
python3 tutorial/18_ri_validation/make_ri_datasets.py          # 데이터 + expected.json 생성
PYTHONPATH=tametools/src python3 tutorial/18_ri_validation/verify_ri.py   # 실제 CLI 실행·대조
```

`verify_ri.py`는 실제 CLI를 호출하고 ground truth와 비교한다. 전부 통과하면 `총 N/N PASS`로 끝난다.

## 검증 시나리오 (예시 파일)

| 파일 | 시나리오 | 검증 포인트 |
| --- | --- | --- |
| `ri_dist.tame` | 정규 N(100,10) 2000개, 로그정규(우편향) 2000개 | 코어 비모수 분위수 정확성, 왜도 처리 |
| `ri_partition.tame` | 성별(M 110±8 / F 90±8), 연령 추세 | TESTNAME+SEX / +AGE 층화 정확성 |
| `ri_n.tame` | n=30 / 60 / 200 | 신뢰도 등급(insufficient_n / review / ok) |
| `ri_outliers.tame` | 정규 500 + 극단치 5개 | Tukey 이상치 제거 |
| `ri_robust_rank.tame` | review 표본 오염, n=120 순위 CI | `METHOD=ROBUST`, `CI_METHOD=RANK` |

이 예제는 기존 `REFERENCE_INTERVAL` 플러그인을 대상으로 한다. `RI_EP28` 사용법은
[참고구간 플러그인 안내](../../docs/REFERENCE_INTERVAL_EP28_GUIDE_KO.md)를 참고한다.
