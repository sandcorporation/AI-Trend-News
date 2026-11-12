#!/usr/bin/env bash
# 배포 전 검사: `make check`와 같은 것(lint·typecheck·시험)을 도커 안에서 돌린다.
# 로컬에서 make check와 같은 검사를 격리된 도커 환경에서 실행한다.
# CI 검사는 GitHub Actions(.github/workflows/pr-check.yml)가 이 스크립트와 같은 검사를 도커 없이 실행한다.
# 노드에 uv·pnpm이 없어도 되고, 로컬에서도 그대로 돈다.
#
#   bash scripts/ci/check.sh
#
# 시험 DB는 이 실행만의 MySQL 컨테이너(mysql:26.10.0, 같은 콜레이션)를 이 실행만의 네트워크에 띄운다 — 호스트 포트를
# 열지 않아 다른 작업(make db의 15443 등)과 겹치지 않는다. 끝나면 컨테이너·네트워크·검사 이미지 태그를 지운다.
# 비공개 의존성(sandcorp-authkit)은 BuildKit ssh로 받는다: GIT_SSH_KEY가 있으면 임시 ssh-agent, 없으면 현재 사용자의 agent.
set -euo pipefail
cd "$(dirname "$0")/../.."

run_id="news-check-$(date +%s)-$$"
image="news-check:$run_id"
net="$run_id"
db="$run_id-db"

cleanup() {
  docker rm -f "$db" >/dev/null 2>&1 || true
  docker network rm "$net" >/dev/null 2>&1 || true
  docker image rm "$image" >/dev/null 2>&1 || true
  if [ -n "${SSH_AGENT_PID:-}" ]; then ssh-agent -k >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM

if [ -n "${GIT_SSH_KEY:-}" ]; then
  eval "$(ssh-agent -s)" >/dev/null
  ssh-add "$GIT_SSH_KEY" 2>/dev/null
fi

echo "== 검사 이미지"
DOCKER_BUILDKIT=1 docker build --ssh default -f scripts/ci/check.Dockerfile -t "$image" .

echo "== 시험 DB (mysql:26.10.0, 네트워크 $net)"
docker network create "$net" >/dev/null
docker run -d --name "$db" --network "$net" --network-alias db \
  -e MYSQL_DATABASE=news -e MYSQL_USER=news -e MYSQL_PASSWORD=news -e MYSQL_ROOT_PASSWORD=news-root \
  mysql:26.10.0 \
  --character-set-server=utf8mb4 --collation-server=utf8mb4_0900_as_cs \
  --skip-log-bin --innodb-flush-log-at-trx-commit=2 --innodb-doublewrite=OFF >/dev/null
ready=0
for _ in $(seq 1 90); do
  # root 로 붙는 것만으로는 초기화 중일 수 있다 — 앱 사용자로 news DB 에 붙어야 준비된 것이다.
  if docker exec "$db" mysql -unews -pnews -h127.0.0.1 -e 'SELECT 1' news >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done
if [ "$ready" != 1 ]; then
  echo "시험 DB가 뜨지 않았다" >&2
  docker logs --tail 40 "$db" >&2 || true
  exit 1
fi
# pytest-django 가 시험 DB(test_news*)를 만들고 지운다(deploy/mysql/dev-init.sql 과 같다).
docker exec "$db" mysql -uroot -pnews-root -e 'GRANT ALL PRIVILEGES ON `test\_news%`.* TO '"'news'@'%'"';' 2>/dev/null

echo "== make check (lint · typecheck · test)"
# make test-backend 는 make db(docker compose)를 부르므로 컨테이너 안에서는 pytest 를 바로 부른다.
docker run --rm --network "$net" -e DB_HOST=db -e DB_PORT=3306 \
  -e NEWS_TEST_CPU_LIMIT="${NEWS_TEST_CPU_LIMIT:-1.0}" "$image" \
  sh -c 'make lint typecheck test-frontend && cd backend && uv run pytest -q'
echo "== 검사 통과"
