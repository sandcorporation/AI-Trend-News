# Postgres → MySQL(OCI HeatWave) 데이터 이전 절차 (#58)

조율자가 따른다. 작업자는 운영 호스트·운영 DB에 손대지 않는다. 결정과 근거는 `docs/adr/0004-mysql-heatwave-always-free.md`.

전제: HeatWave에 DB `news`가 `utf8mb4` / `utf8mb4_0900_as_cs`로 만들어져 있고, 서비스 계정이 그 DB에 전체 권한을 가진다. 아래 `dc`는 oracle 호스트의 Jenkins 워크스페이스(`/var/jenkins_home/workspace/news`)에서 쓰는 다음 별칭이다.

```sh
dc() { COMPOSE_PROJECT_NAME=news COMPOSE_FILE=docker-compose.prod.yml:docker-compose.oracle.yml docker compose "$@"; }
```

## 0. 건수 세는 스크립트

옛 이미지와 새 이미지에서 똑같이 돌린다(`manage.py shell`에 표준 입력으로).

```python
from django.apps import apps
from django.db.models import Max
for m in apps.get_models():
    if m._meta.app_label in {"contenttypes", "sessions", "admin"} or m._meta.label == "auth.Permission":
        continue
    print(f"{m._meta.label}\t{m.objects.count()}\tmax_pk={m.objects.aggregate(x=Max('pk'))['x']}")
```

`counts.py`로 저장해 두고 `dc run --rm -T --no-deps web python manage.py shell < counts.py | grep -v imported > counts-<pg|mysql>.tsv`.

## 1. 쓰기 중지와 덤프 (병합 전, 옛 이미지)

1. 편집자 클라우드 예약 실행이 이 구간에 걸리지 않는지 본다(걸리면 끝난 뒤에).
2. 쓰기를 멈춘다: `dc stop scheduler web`. web도 조회 수(`ArticleView`)·유입 클릭·문의·MCP 편집 도구로 쓰므로 함께 멈춘다(몇 분 동안 502). 다운타임을 피하려면 web을 두고 그 사이의 조회·클릭을 잃는 쪽을 택해도 된다 — 그때도 MCP 편집은 하지 않는다.
3. 덤프(옛 이미지, postgres는 떠 있는 그대로):
   ```sh
   dc run --rm -T --no-deps web python manage.py dumpdata --natural-foreign --indent 0 \
     -e contenttypes -e auth.permission -e sessions -e admin.logentry > news-dump.json
   dc run --rm -T --no-deps web python manage.py shell < counts.py | grep -v imported > counts-pg.tsv
   chmod 644 news-dump.json
   ```
   빼는 것: `contenttypes`·`auth.permission`(새 DB의 `migrate`가 다시 만든다), `sessions`(admin 로그인만 다시 하면 된다), `admin.logentry`(admin 변경 기록, 버려도 된다). 이 저장소에 캐시 테이블은 없다. `inquiries.SubmitAttempt`(문의 제출 횟수 제한 기록)는 작아서 그대로 옮긴다. 그 밖의 앱(collection·articles·editor·inquiries·posts)과 `auth.user`·`auth.group`은 모두 옮긴다.
4. 덤프 크기와 `counts-pg.tsv`를 남겨 둔다. 옛 `postgres` 컨테이너와 `pgdata` 볼륨은 지우지 않는다(되돌리기용).

## 2. env 바꾸기

워크스페이스 `.env`에서 `POSTGRES_DB`·`POSTGRES_USER`·`POSTGRES_PASSWORD`를 지우고 `DB_HOST`(HeatWave 사설 IP)·`DB_PORT=3306`·`DB_NAME=news`·`DB_USER`·`DB_PASSWORD`를 넣는다(`.env.example` 참고).

## 3. 병합·배포 (새 이미지)

PR 병합 뒤 GitHub Actions가 검증·운영 이미지를 빌드하고 `production`으로 승격한다. Jenkins는 GHCR의 Actions 게시 이미지를 배포한다: pull → `wait_for_db` → 빈 HeatWave에 `migrate` → web·scheduler 교체 → `/api/health`. 새 compose에는 `postgres` 서비스가 없으므로 옛 postgres 컨테이너는 고아로 남아 계속 돈다(그대로 둔다).

## 4. 적재 (새 이미지)

배포가 끝나자마자:

```sh
dc stop scheduler                       # 시작 즉시 돈 check_alerts 등이 Alert 행을 만들었을 수 있다
dc run --rm -T --no-deps web python manage.py flush --noinput   # 스키마는 두고 행만 비운다
dc run --rm -T --no-deps -v "$PWD/news-dump.json:/tmp/news-dump.json:ro" web \
  python manage.py loaddata /tmp/news-dump.json
dc run --rm -T --no-deps web python manage.py shell < counts.py | grep -v imported > counts-mysql.tsv
diff counts-pg.tsv counts-mysql.tsv && echo 건수 일치
dc start scheduler
```

`flush`를 빼면 배포 직후 스케줄러가 만든 `Alert(kind, target)` 행이 덤프의 같은 키와 부딪혀 `loaddata`가 유일 제약 오류로 멈춘다. `max_pk`까지 같으면 AUTO_INCREMENT도 이어받은 것이다(MySQL은 다음 INSERT에서 최댓값+1을 쓴다; 시퀀스 재설정 불필요).

## 5. 스모크

- `/api/health` → `{"status": "ok", "db": "ok"}`
- `/`, 게시된 기사 하나 `/a/<slug>` 200, 같은 slug를 대문자로 바꾼 주소 404(콜레이션 확인), 옛 slug `/a/<옛 slug>` 301
- `/predictions`, `/sitemap.xml` 200
- admin 로그인(sandcorp-auth) 후 기사 목록 「7일 조회」 칼럼, 「유입 링크 클릭」 집계
- 다음 정시 스케줄러 로그: `collect` 결과 문구, `주기 끝: 실패 없음`

## 6. 되돌리기와 정리

- 되돌리기: PR을 revert해 배포하고 `.env`를 `POSTGRES_*`로 되돌린다. 옛 postgres 컨테이너·`pgdata`가 남아 있으므로 그대로 붙는다(전환 뒤 HeatWave에 쓰인 것은 잃는다).
- 정리(며칠 뒤 문제가 없으면): `docker stop news-postgres-1 && docker rm news-postgres-1`, `docker volume rm news_pgdata`.

## 로컬 리허설 결과 (2026-10-23)

- 원본: 임시 Postgres 17 컨테이너에 옛 코드(main `f3649c2`)로 `migrate` 후 시드(모든 앱 테이블, 197객체: 기사 20·수집물 40·분석/전망 5·조회 20·클릭 20·포스트 5·경보 2·문의 1 등). JSON(`html_rules`·`predictions`), bool·null bool, NULL datetime, 빈 문자열, 이모지, 대소문자만 다른 태그(`OpenAI`/`openai`)·악센트(`café`/`cafe`)를 일부러 넣었다.
- 덤프: 위 1-3의 명령 그대로(47KB).
- 적재: `make prod-local`(새 이미지 + 로컬 `mysql:26.10.0`, as_cs) → `migrate` 오류 없음 → 스케줄러 정지 → `loaddata` 「Installed 197 object(s)」 3.6초.
- 대조: 모델 20개의 건수·`max_pk`가 모두 일치. MySQL에서 다시 덤프한 JSON과 원본이 객체 197개 모두 같다(차이는 M2M `tags` 목록의 순서뿐). 새 `Tag`·`Item` pk가 6·41로 이어졌다.
- 스모크: `/` 200, `/a/article-1` 200, `/a/ARTICLE-1` 404, `/a/old-article-4` 301, `/?category=old-ai` 301, `/predictions` 200, `/sitemap.xml` 200.
- 주의: `dumpdata`는 datetime을 밀리초까지만 쓴다(원본 마이크로초는 이전 때 잘린다). 이번 리허설에서 처음 배포 직후 스케줄러가 `Alert` 한 행을 만들어 둔 것을 확인했다 — 그래서 4의 `flush`를 넣었다.
