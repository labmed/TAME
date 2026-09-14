# GitHub 게시 안내 — tametools 0.4.0

## 저장소 Code에 올릴 내용

이 폴더의 프로그램 소스, 웹앱, 테스트, 예제, 사용 문서, 빌드 스크립트를 사용합니다.
공개용으로 정리한 Git 이력이 포함되어 있습니다. Git으로 추가할 때 `.gitignore`가
배포물과 의존성·캐시 폴더를 제외합니다. 변경 사항은 `git status`와 `git diff`로 확인하세요.
기존 원격 저장소에 다른 이력이 있으면 일반 push가 거절될 수 있습니다. 먼저 이력을
비교하고, 새 저장소 게시 또는 원격 이력 교체 방식을 결정하세요.

## Releases에 첨부할 내용

릴리스 태그는 게시할 소스 커밋의 `v0.4.0`으로 지정하고,
[릴리스 설명](RELEASE_NOTES_v0.4.0.md)을 사용합니다.
`dist/0.4.0/` 바로 아래의 다음 파일들을 첨부합니다.

| 파일 | 용도 |
|---|---|
| `tametools-0.4.0-x64.msi` | Windows 설치형 |
| `tametools-0.4.0-windows-x64-portable.zip` | Python 설치 없이 실행하는 Windows 전체 폴더 |
| `tametools-0.4.0-browser-local.zip` | Python을 이용해 개인 PC의 Chrome에서 실행; 빌드된 화면 포함 |
| `tametools-0.4.0-py3-none-any.whl` | Python 라이브러리·CLI 설치 |
| `tametools-0.4.0.tar.gz` | Python 소스 배포본 |
| `tametools-0.4.0-source.zip` | 웹앱·문서·예제를 포함한 전체 프로그램 소스 |
| `SHA256SUMS.txt`, `SOURCE_SHA256.json` | 배포 파일 및 소스 체크섬 |
| `BUILD_VERIFICATION.json`, `windows-build-requirements.txt` | 빌드 검증 결과와 의존성 버전 |

`dist/0.4.0/windows-exe/`는 portable ZIP과 MSI를 만드는 작업 폴더입니다.
그 안의 EXE 한 개만 따로 전달하면 공유 라이브러리가 없어 실행되지 않습니다.
브라우저 ZIP은 첫 실행 때 Python 패키지를 설치하므로 인터넷이 필요합니다.
GitHub가 자동 제공하는 소스 ZIP에는 빌드된 웹 화면이 없으므로 사용자는 위의
`browser-local.zip`을 내려받아야 합니다.

## 재빌드

[빌드 절차](BUILD_AND_RELEASE.md)를 따릅니다. `scripts/package_release.py`는
현재 소스로 배포 ZIP과 체크섬을 만들며 GitHub에 전송하지 않습니다.
