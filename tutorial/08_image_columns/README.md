# 08. Image Columns

이 예제는 이미지를 `IMAGE::B64` 컬럼에 저장하고, 폴더로 추출한 뒤 `IMAGE::PATH` 컬럼으로 바꾸는 과정을 보여준다.

## 핵심 규칙

- 내장 이미지: `[[IMAGE::B64]]썸네일`
- 파일 경로 이미지: `[[IMAGE::PATH]]썸네일`
- 권장 저장 형식은 `data:image/png;base64,...` 같은 data URL이다.

## 예제 파일

- `sample_images_embedded.tame`

## 실행 예

이미지를 폴더로 추출하고 경로형 데이터셋 저장:

```bash
tametools extract-images \
  tutorial/08_image_columns/sample_images_embedded.tame \
  --output-dir tutorial/08_image_columns/extracted \
  --output tutorial/08_image_columns/sample_images_paths.tame
```

경로형 데이터셋을 다시 base64 내장형으로 변환:

```bash
tametools embed-images \
  tutorial/08_image_columns/sample_images_paths.tame \
  --output tutorial/08_image_columns/sample_images_embedded_roundtrip.tame
```

## 활용

- 이미지와 표형 메타데이터를 한 `.tame` 파일에 같이 저장할 수 있다.
- 필요할 때만 추출해서 머신러닝용 폴더 구조로 바꿀 수 있다.
- 다시 내장형으로 묶어서 배포하거나 재현성을 높일 수 있다.
