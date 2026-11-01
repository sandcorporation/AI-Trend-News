# 이미지를 oracle 밖에서 굽기 (#142)

oracle(aarch64, 4코어)은 운영 서비스 열 개 남짓이 CPU를 나눠 쓴다. 배포할 때마다 거기서 이미지를 빌드하면(`sandcorp-news` 전체 109초) 운영 서비스가 느려진다.
그래서 **이미지는 다른 노드에서 굽고 ghcr에 올리며, oracle은 받아서 교체만 한다.** news가 첫 사례이고, 다른 저장소는 아래 「옮기는 법」을 따른다.

## 흐름

```
main push ─▶ 이미지 빌드 ─────────────────────▶ 배포(oracle)
             macos (M4 Pro, arm64 네이티브)       docker pull ghcr.io/sandcorporation/sandcorp-news:<sha>
             └ 3분 안에 실행기를 못 잡으면        wait_for_db → migrate → web·scheduler 교체 → 헬스 확인
               home(built-in, QEMU로 arm64)         (빌드 없음)
```

- **노드를 못 잡았을 때만** home로 넘어간다. macos에서 빌드가 실패하면 실패로 끝낸다(다시 home로 굽지 않는다).
- 빌드 노드는 콘솔(`== 빌드 노드: macos (...)`)과 빌드 설명(`<sha12> · 빌드 macos · oracle pull`)·디스코드 알림에 남는다.
- 이미지: `ghcr.io/sandcorporation/<이름>:<커밋 sha 40자>`, 레이블 `org.opencontainers.image.source`·`revision`. 빌드 캐시는 `<이름>-buildcache:arm64`(레지스트리, `mode=max`).
- 자격: ghcr 쓰기 `ghcr-push-token`(Secret text, codemit과 공유), 비공개 의존성용 SSH `github-farrar142`. oracle은 호스트 docker 설정에 있는 ghcr **읽기** 로그인을 쓴다(sandcorp-flow와 같다).

## 파라미터

| 이름 | 기본 | 뜻 |
|---|---|---|
| `BUILDER` | `auto` | `auto` / `macos` / `home` — 시험용으로 빌드 노드를 강제 |
| `MAC_WAIT_SECONDS` | `180` | auto에서 macos 실행기를 기다리는 시간. 넘으면 home |
| `ROLLBACK_SHA` | 빈 값 | 채우면 빌드 없이 그 커밋의 이미지로 되돌린다 |

## 이전 sha로 되돌리기

Jenkins `sandcorp-news`를 「Build with Parameters」로 `ROLLBACK_SHA=<되돌릴 커밋 sha 40자>`를 넣고 돌린다. 빌드 단계는 건너뛰고 oracle이 그 sha 이미지를 pull해 같은 migrate·교체·헬스 절차를 밟는다.
- ghcr에 그 sha 이미지가 있어야 한다(정리 잡 `ghcr-prune`이 오래된 것을 지운다).
- migrate는 앞으로만 간다. 되돌리는 코드가 새 스키마를 못 읽으면 DB를 따로 되돌려야 한다(파괴적 스키마 변경은 두 번에 나눠 배포하는 규칙 그대로).

## 옮기는 법 (slack 봇·metrics·portfolio·auth·daypage)

1. **스크립트 한 개**: `scripts/ci/build-image.sh <macos|home>`를 복사하고 `NEWS_IMAGE_REPO` 기본값과 `source` 레이블의 저장소 이름만 바꾼다. 임시 `DOCKER_CONFIG`에 토큰을 적는 방식이라 노드의 docker 설정·키체인을 건드리지 않는다. bash 3.2(macOS)에서 돈다. 비공개 의존성이 없으면 `GIT_SSH_KEY`·`--ssh default`를 뺀다.
2. **Jenkinsfile**: 이 저장소의 `buildImage()`·「이미지 빌드」·「배포」 단계를 복사한다. 이미지 이름(`IMAGE_REPO`)과 디스코드 표식만 바꾼다. 조각의 뼈대:
   ```groovy
   parallel(failFast: true,
     'macos': { node('macos') { env.MAC_ACQUIRED = 'true'; buildImage('macos') } },
     '대기 감시': { /* MAC_ACQUIRED 가 true 가 될 때까지 최대 N초 sleep, 아니면 error() */ })
   // catch: 실행기를 잡기 전이면 node('laurio-staging') { buildImage('home') }, 잡은 뒤면 rethrow
   ```
3. **compose**: 서비스의 `image:`를 `${<APP>_IMAGE:-<로컬 이름>:latest}`로 바꾼다. 로컬(`make prod-local`)은 변수가 없어 빌드하고, Jenkins는 `<APP>_IMAGE=ghcr…:<sha>`를 줘서 pull만 한다. `up`에는 `--no-build`를 붙인다(`run`에는 이 옵션이 없지만 이미지가 있으면 빌드하지 않는다).
4. **deploy.sh**: 이미지 변수가 있으면 `docker pull`만 하고 `build`와 ssh-agent는 건너뛴다. 끝에 최신 3개만 남기고 `docker rmi`.
5. **oracle 노드**: 그 호스트의 docker가 `ghcr.io`에 읽기 로그인돼 있어야 한다.

## 주의점

- **Jenkins 스크립트 승인이 필요 없는 단계만 쓴다.** `Jenkins.instance`·`nodesByLabel`류를 쓰지 않고, 노드 대기 시간 제한은 `parallel(failFast)` + `sleep` 감시로 만든다. `timeout { node {} }`는 빌드 본문까지 같은 시계로 잘라 쓰지 않는다.
- macos 실행기는 1개이고 codemit 열차와 나눠 쓴다. 대기가 3분을 넘으면 home로 간다.
- home는 QEMU로 arm64를 굽는다. 캐시가 없으면 느리지만 레지스트리 캐시(`mode=max`)가 두 노드에서 공유돼 다음부터 빨라진다.
- 같은 잡을 oracle에서 워크스페이스 `.env`와 함께 돌리므로 배포 단계의 체크아웃은 `news` 워크스페이스를 그대로 쓴다.
- 빌드 노드의 `buildx`에 `docker-container` 빌더가 필요하다(스크립트가 `news-images`를 만든다).
