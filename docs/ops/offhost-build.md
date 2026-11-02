# 이미지를 oracle 밖에서 굽기 (#142) · 공용 라이브러리 (jenkins-shared #3)

oracle(aarch64, 4코어)은 운영 서비스 열 개 남짓이 CPU를 나눠 쓴다. 배포할 때마다 거기서 이미지를 빌드하면(`sandcorp-news` 전체 109초) 운영 서비스가 느려진다.
그래서 **이미지는 다른 노드에서 굽고 ghcr에 올리며, oracle은 받아서 교체만 한다.** news가 첫 사례였고, 지금은 그 패턴이 공용 라이브러리로 옮겨졌다.

파이프라인은 Jenkins 공용 라이브러리 `sandcorp`(저장소 sandcorporation/jenkins-shared, `vars/sandcorpDeploy.groovy`)에 있다.
이 저장소의 `Jenkinsfile`은 `@Library('sandcorp') _`와 `sandcorpDeploy(...)` 호출 하나이고 이 저장소가 넘기는 값만 담는다.
공통 동작·인자 목록·노드 라벨을 바꾸는 곳은 jenkins-shared `README.md`가 원문이다. 다른 저장소로 옮길 때도 그 README를 본다.

## 흐름

```
GitHub push ─▶ 배포할 커밋 확인 ─▶ 이미지 빌드 ─────────────────────▶ 배포(oracle)
               (built-in)           macos (M4 Pro, arm64 네이티브)      docker pull ghcr.io/sandcorporation/sandcorp-news:<sha>
               main 이 지난 배포     └ 3분 안에 실행기를 못 잡으면        wait_for_db → migrate → web 교체 → healthy
               뒤로 안 움직였으면      home(built-in, QEMU로 arm64)       → scheduler 교체 → 헬스 확인
               NOT_BUILT로 끝         검사(scripts/ci/check.sh) 먼저      헬스 실패 → 직전 성공 이미지로 자동 롤백
```

- **작업 브랜치 push는 배포 없이 끝난다.** 웹훅은 브랜치 생성·push·삭제에도 잡을 깨우지만, 잡은 늘 main을 받는다. main sha가 직전 성공 빌드의 sha와 같으면 `NOT_BUILT`(디스코드 알림 없음).
- **배포 전 검사**: 빌드 노드에서 이미지를 굽기 전에 `bash scripts/ci/check.sh`가 `make check`와 같은 것(lint·typecheck·시험)을 도커 안에서 돌린다. 운영 이미지 빌드(`Dockerfile`)는 `pnpm build`(tsc 포함)만 하고 백엔드 시험·린트는 하지 않아서 따로 돈다. 실패하면 굽지도 배포하지도 않는다(GitHub 무료 요금제라 병합을 막지 못해 여기서 막는다). 같은 검사가 PR 때도 돈다: 멀티브랜치 잡 `sandcorp-news-pr`(`Jenkinsfile.pr`, 라이브러리 `sandcorpPrCheck`)가 파괴적 migrate 검사와 함께 돌려 PR 상태로 올리고, 작업자는 `wait-ci.py` 통과 뒤 병합한다.
- **노드를 못 잡았을 때만** home으로 넘어간다. macos에서 검사·빌드가 실패하면 실패로 끝낸다.
- 빌드 노드는 콘솔(`== 빌드 노드: macos (...)`)과 빌드 설명(`<sha12> · 빌드 macos · oracle pull`)·디스코드 알림(`[Foresight]`)에 남는다.
- 이미지: `ghcr.io/sandcorporation/sandcorp-news:<커밋 sha 40자>`, 레이블 `org.opencontainers.image.source`·`revision`. 빌드 캐시는 `sandcorp-news-buildcache:arm64`(레지스트리, `mode=max`, buildx 빌더 `news-images`).
- 자격: ghcr 쓰기 `ghcr-push-token`, 비공개 의존성용 SSH `github-farrar142`, 디스코드 `discord-deploy-webhook`. oracle은 호스트 docker 설정에 있는 ghcr **읽기** 로그인을 쓴다.

## 파라미터

| 이름 | 기본 | 뜻 |
|---|---|---|
| `BUILDER` | `auto` | `auto` / `macos` / `home` — 시험용으로 빌드 노드를 강제 |
| `MAC_WAIT_SECONDS` | `180` | auto에서 macos 실행기를 기다리는 시간. 넘으면 home |
| `ROLLBACK_SHA` | 빈 값 | 채우면 빌드 없이 그 커밋의 이미지로 되돌린다(이미 배포한 sha여도) |

## 자동 롤백

web을 새 이미지로 바꾼 뒤 healthy가 안 되거나 헬스 확인이 실패하면, 직전 성공 빌드의 sha 이미지로 web·scheduler를 다시 바꾸고 헬스를 다시 본다. migrate는 되돌리지 않는다. 빌드는 `FAILURE`, 디스코드에는 「↩️ 자동 롤백 — <새 sha> 헬스 실패, <옛 sha>로 되돌림」. pull·DB 대기·migrate에서 멈추면 web은 아직 옛 이미지라 되돌릴 것이 없다.
같은 sha는 push가 다시 깨워도 배포하지 않는다(깨진 커밋이 브랜치 push마다 배포·롤백을 되풀이하지 않게). 다시 해 보려면 「Build Now」.

## 이전 sha로 되돌리기

Jenkins `sandcorp-news`를 「Build with Parameters」로 `ROLLBACK_SHA=<되돌릴 커밋 sha 40자>`를 넣고 돌린다. 빌드 단계는 건너뛰고 oracle이 그 sha 이미지를 pull해 같은 migrate·교체·헬스 절차를 밟는다.
- ghcr에 그 sha 이미지가 있어야 한다(정리 잡 `ghcr-prune`이 오래된 것을 지운다).
- migrate는 앞으로만 간다. 되돌리는 코드가 새 스키마를 못 읽으면 DB를 따로 되돌려야 한다(파괴적 스키마 변경은 두 번에 나눠 배포하는 규칙 그대로).
- 되돌린 뒤 main이 그대로면 다음 작업 브랜치 push는 건너뛴다(되돌린 상태 유지). 다음 main 병합이 다시 배포한다.

## news 사정

- 이미지 하나를 web·scheduler가 같이 쓴다. 둘 다 `${NEWS_IMAGE:-news-web:latest}`다. 변수가 없으면(`make prod-local`) 로컬에서 빌드한다.
- `deploy/deploy.sh`는 이제 로컬 운영 compose 확인용(`make prod-local`)이다. Jenkins는 라이브러리의 `resources/sandcorp/deploy.sh`를 같은 순서·같은 값(Jenkinsfile)으로 돌린다.
- 운영 DB는 compose 밖 OCI MySQL HeatWave라 DB 확인은 `manage.py wait_for_db --timeout 20`(라이브러리가 10번까지 다시 시도)이다.
- 비공개 의존성 sandcorp-authkit 때문에 빌드 노드에서 `GIT_SSH_KEY`(자격 `github-farrar142`)를 BuildKit `--ssh default`로 넘긴다(`buildSsh: true`, 검사 이미지도 같다).
- 배포 단계는 oracle의 같은 잡 워크스페이스(`/home/jenkins/agent/workspace/sandcorp-news`, 운영 `.env`가 있는 곳)를 그대로 쓴다.
- 검사의 시험 DB는 그 실행만의 MySQL 컨테이너·네트워크(호스트 포트 없음)라 같은 노드의 다른 작업과 겹치지 않는다.

## 주의점

- macos 실행기는 1개이고 다른 열차와 나눠 쓴다. 대기가 3분을 넘으면 home으로 간다. home은 QEMU라 캐시가 없으면 느리다(레지스트리 캐시 `mode=max`를 두 노드가 공유). 검사 이미지는 노드의 네이티브 아키텍처로 굽는다.
- 병합 뒤 첫 빌드가 돌아야 파라미터가 잡에 등록된다.
