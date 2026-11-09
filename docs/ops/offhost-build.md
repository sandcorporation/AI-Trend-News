# 이미지를 oracle 밖에서 굽기 (#142) · 공용 라이브러리 (jenkins-shared #3)

oracle(aarch64, 4코어)은 운영 서비스 열 개 남짓이 CPU를 나눠 쓴다. 배포할 때마다 거기서 이미지를 빌드하면(`sandcorp-news` 전체 109초) 운영 서비스가 느려진다.
그래서 **이미지는 다른 노드에서 굽고 ghcr에 올리며, oracle은 받아서 교체만 한다.** news가 첫 사례였고, 지금은 그 패턴이 공용 라이브러리로 옮겨졌다.

파이프라인은 Jenkins 공용 라이브러리 `sandcorp`(저장소 sandcorporation/jenkins-shared, `vars/sandcorpDeploy.groovy`)에 있다.
이 저장소의 `Jenkinsfile`은 `@Library('sandcorp') _`와 `sandcorpDeploy(...)` 호출 하나이고 이 저장소가 넘기는 값만 담는다.
공통 동작·인자 목록·노드 라벨을 바꾸는 곳은 jenkins-shared `README.md`가 원문이다. 다른 저장소로 옮길 때도 그 README를 본다.

## 흐름 (#180: Actions 이미지 빌드 + production 자동 승격)

```
PR ─▶ Actions pr-check ─▶ main 병합
      ─▶ release.yml(push main): check(pr-check 재사용) ─▶ image(image.yml, ubuntu-24.04-arm, ghcr <sha>) ─▶ promote(production fast-forward)
      ─▶ Jenkins sandcorp-news(production push만): 이미지가 ghcr에 있는지 확인(5분) ─▶ 배포(oracle)
                                                  docker pull ghcr.io/sandcorporation/sandcorp-news:<sha>
                                                  wait_for_db → migrate → web 교체 → healthy → scheduler 교체 → 헬스
                                                  헬스 실패 → 직전 성공 이미지로 자동 롤백
```

- **운영 = `production` 브랜치 머리.** 사람이 push하지 않는다. `release.yml`의 promote 잡이 `scripts/ci/promote.sh ff <sha>`로 앞으로만 옮긴다(옛 실행이 늦게 끝나면 건너뜀, 갈라지면 실패). 브랜치에는 ruleset(강제 push·삭제 금지)이 걸린다.
- **Jenkins는 굽지도 검사하지도 않는다**(`deployFrom: 'production'`, jenkins-shared README 「승격 브랜치에서 배포」). main push로도 깨지만 production이 안 움직였으면 「이미 배포한 sha」로 `NOT_BUILT`.
- **이미지**: `ghcr.io/sandcorporation/sandcorp-news:<커밋 sha 40자>`(web·scheduler 공용), 라벨 `org.opencontainers.image.source`·`revision`, provenance 없음. 빌드 캐시는 같은 패키지의 `:buildcache`(레지스트리, `mode=max`). Actions 빌드는 `AUTHKIT_VIA_HTTPS=1`로 공개 sandcorp-auth를 https로 받는다(비밀 없음).
- **검사**: release의 check는 PR 검사와 같은 `pr-check.yml`(workflow_call)이다. 깨지면 이미지도 승격도 없다.
- 빌드 노드는 빌드 설명·디스코드 알림(`[Foresight]`)에 `actions`로 남는다.
- 자격: Actions는 `GITHUB_TOKEN` 하나(image 잡 `packages: write`, promote 잡 `contents: write`). Jenkins 비상 길은 ghcr 쓰기 `ghcr-push-token`, SSH `github-farrar142`. 디스코드 `discord-deploy-webhook`. oracle은 호스트 docker 설정의 ghcr **읽기** 로그인을 쓴다.

## 비상 길: Jenkins가 oracle 밖에서 굽는다 (`BUILD_ON=jenkins`)

Actions가 못 구울 때 「Build with Parameters」로 `BUILD_ON=jenkins`. 옛 흐름 그대로 macos(M4 Pro, arm64 네이티브, `MAC_WAIT_SECONDS` 안에 못 잡으면 home QEMU)에서 `bash scripts/ci/check.sh`(`make check`를 도커 안에서) 뒤 굽고 배포한다. production 머리 sha를 굽는다. 캐시는 `sandcorp-news-buildcache:arm64`.

## 파라미터

| 이름 | 기본 | 뜻 |
|---|---|---|
| `BUILD_ON` | `actions` | `actions`(Actions가 구운 이미지를 기다려 배포) / `jenkins`(비상 길: Jenkins가 검사·빌드) |
| `BUILDER` | `auto` | `auto` / `macos` / `home` — 시험용으로 빌드 노드를 강제 |
| `MAC_WAIT_SECONDS` | `180` | auto에서 macos 실행기를 기다리는 시간. 넘으면 home |
| `ROLLBACK_SHA` | 빈 값 | 채우면 빌드 없이 그 커밋의 이미지로 되돌린다(이미 배포한 sha여도) |

## 자동 롤백

web을 새 이미지로 바꾼 뒤 healthy가 안 되거나 헬스 확인이 실패하면, 직전 성공 빌드의 sha 이미지로 web·scheduler를 다시 바꾸고 헬스를 다시 본다. migrate는 되돌리지 않는다. 빌드는 `FAILURE`, 디스코드에는 「↩️ 자동 롤백 — <새 sha> 헬스 실패, <옛 sha>로 되돌림」. pull·DB 대기·migrate에서 멈추면 web은 아직 옛 이미지라 되돌릴 것이 없다.
같은 sha는 push가 다시 깨워도 배포하지 않는다(깨진 커밋이 브랜치 push마다 배포·롤백을 되풀이하지 않게). 다시 해 보려면 「Build Now」.

## 되돌리기

production은 앞으로만 간다. production을 옛 sha로 옮기는 워크플로는 두지 않는다(조직 ruleset이 Actions를 bypass로 받지 않는다).

1. **정석: main에 revert PR.** 병합하면 release.yml이 검사·이미지·승격을 그대로 타고 Jenkins가 배포한다.
2. **급하면: Jenkins `ROLLBACK_SHA`.** `sandcorp-news`를 「Build with Parameters」로 `ROLLBACK_SHA=<되돌릴 커밋 sha 40자>`. 빌드 없이 oracle이 그 sha 이미지를 pull해 같은 migrate·교체·헬스 절차를 밟는다. production은 그대로라 다음 승격이 다시 앞으로 간다 — 그 전에 revert PR을 넣는다.
- ghcr에 그 sha 이미지가 있어야 한다(정리 잡 `ghcr-prune`이 오래된 것을 지운다).
- migrate는 앞으로만 간다. 되돌리는 코드가 새 스키마를 못 읽으면 DB를 따로 되돌려야 한다(파괴적 스키마 변경은 두 번에 나눠 배포하는 규칙 그대로).

## news 사정

- 이미지 하나를 web·scheduler가 같이 쓴다. 둘 다 `${NEWS_IMAGE:-news-web:latest}`다. 변수가 없으면(`make prod-local`) 로컬에서 빌드한다.
- `deploy/deploy.sh`는 이제 로컬 운영 compose 확인용(`make prod-local`)이다. Jenkins는 라이브러리의 `resources/sandcorp/deploy.sh`를 같은 순서·같은 값(Jenkinsfile)으로 돌린다.
- 운영 DB는 compose 밖 OCI MySQL HeatWave라 DB 확인은 `manage.py wait_for_db --timeout 20`(라이브러리가 10번까지 다시 시도)이다.
- sandcorp-authkit(공개)은 Actions에서는 https로, 비상 길에서는 `GIT_SSH_KEY`(자격 `github-farrar142`)를 BuildKit `--ssh default`로 받는다(`buildSsh: true`, 검사 이미지도 같다).
- 배포 단계는 oracle의 같은 잡 워크스페이스(`/home/jenkins/agent/workspace/sandcorp-news`, 운영 `.env`가 있는 곳)를 그대로 쓴다.
- 검사의 시험 DB는 그 실행만의 MySQL 컨테이너·네트워크(호스트 포트 없음)라 같은 노드의 다른 작업과 겹치지 않는다.

## 주의점

- (비상 길) macos 실행기는 1개이고 다른 열차와 나눠 쓴다. 대기가 3분을 넘으면 home으로 간다. home은 QEMU라 캐시가 없으면 느리다(레지스트리 캐시 `mode=max`를 두 노드가 공유). 검사 이미지는 노드의 네이티브 아키텍처로 굽는다.
- 병합 뒤 첫 빌드가 돌아야 파라미터(`BUILD_ON`)가 잡에 등록된다.
- Jenkins 잡 브랜치 지정자가 `*/production`이어야 배포가 돈다(main이면 「배포 브랜치가 아니다」로 `NOT_BUILT`).
