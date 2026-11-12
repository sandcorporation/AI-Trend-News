# GitHub Actions 빌드와 Jenkins 배포

CI, PR 검사, 테스트, 운영 이미지 빌드는 GitHub Actions에서만 실행한다. Jenkins는 `production` 승격 브랜치의 변경을 받아 Actions가 GHCR에 게시한 이미지를 Oracle에 배포하는 역할만 한다. Jenkins 작업은 macOS, home 또는 다른 Jenkins 노드에서 검사하거나 이미지를 빌드하는 경로를 제공하지 않는다.

Jenkins 파이프라인은 공유 라이브러리 `sandcorp`(저장소 `sandcorporation/jenkins-shared`, `vars/sandcorpDeploy.groovy`)를 사용한다. 이 저장소의 [Jenkinsfile](../../Jenkinsfile)은 라이브러리에 전달할 배포 설정만 가진다. 공유 라이브러리의 `deployFrom: 'production'` 계약 및 공통 동작은 해당 저장소 README를 기준으로 한다.

## 파이프라인

```
PR ─▶ Actions pr-check.yml: 검사·타입 확인·테스트
  └─▶ main 병합 ─▶ Actions release.yml: check(pr-check 재사용) ∥ image(image.yml, ubuntu-24.04-arm, GHCR <sha>)
                      └─ 둘 다 통과하면 production fast-forward
                            └─ Jenkins: production만 감시, GHCR 이미지 확인 후 Oracle 배포
```

- 운영 버전은 `production` 브랜치 머리다. `release.yml`의 promote 잡이 `scripts/ci/promote.sh ff <sha>`로 앞으로만 이동한다.
- Actions는 `ghcr.io/sandcorporation/sandcorp-news:<커밋 sha 40자>`를 `linux/arm64`로 빌드하고 push한다. `web`과 `scheduler`가 같은 이미지를 사용하며 `:buildcache`는 레지스트리 빌드 캐시다.
- `pr-check.yml`은 PR 검증을 하고, `release.yml`은 같은 검사를 이미지 빌드와 병렬로 수행한다. 둘 다 통과해야 `production`으로 승격된다.
- Jenkins는 `production`에서만 배포한다. 예상 이미지가 GHCR에 없으면 짧게 기다린 뒤 실패한다. Jenkins에서 이미지를 만들거나 검사를 재실행하는 대체 경로는 없다.
- Jenkins가 Oracle에서 수행하는 배포 순서는 이미지 pull → DB 대기 → migrate → web 교체 및 헬스 확인 → scheduler 교체다. 헬스 확인이 실패하면 이전 성공 이미지로 자동 롤백한다.
- Actions 인증은 `GITHUB_TOKEN`을 쓴다(image 잡 `packages: write`, promote 잡 `contents: write`). Oracle 호스트는 GHCR 읽기 인증을 사용한다. Jenkins에는 이미지 push 권한이나 빌드용 SSH 자격이 필요하지 않다.

## 되돌리기

정석은 `main`에 revert PR을 병합하는 것이다. Actions가 변경을 검사하고 새 이미지를 게시하며 `production` 승격 후 Jenkins가 그 이미지를 배포한다.

긴급 상황에는 Jenkins의 `ROLLBACK_SHA` 파라미터로 해당 커밋의 GHCR 이미지를 배포할 수 있다. 이 기능은 기존에 게시된 이미지만 소비하며 빌드를 시작하지 않는다. production 브랜치는 바뀌지 않으므로 이후 승격 전에 revert PR을 병합한다. GHCR에서 해당 태그가 정리되지 않았는지 확인한다.

DB 마이그레이션은 자동으로 되돌리지 않는다. 되돌릴 코드가 새 스키마를 사용할 수 없는 경우 DB 복구를 별도로 수행해야 한다.

## 운영 참고

- 로컬에서 운영 compose를 확인하는 명령은 `make prod-local`이다. `deploy/deploy.sh`는 로컬 운영 compose 확인용이며 Jenkins 빌드 진입점이 아니다.
- 운영 DB는 compose 밖의 OCI MySQL HeatWave다. Jenkins 배포는 `manage.py wait_for_db --timeout 20`으로 연결을 확인한다.
- Jenkins 잡의 SCM 브랜치 지정자는 `*/production`이어야 한다. 배포 브랜치가 아니면 라이브러리가 배포를 건너뛴다.
- 액션 이미지와 검사 워크플로는 [image.yml](../../.github/workflows/image.yml), [pr-check.yml](../../.github/workflows/pr-check.yml), [release.yml](../../.github/workflows/release.yml)에 있다.
