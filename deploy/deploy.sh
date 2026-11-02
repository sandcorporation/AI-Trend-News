#!/usr/bin/env bash
# 로컬 운영 compose 확인용(`make prod-local`). Jenkins 운영 배포는 이 파일을 쓰지 않는다 —
# jenkins-shared 라이브러리 sandcorpDeploy의 resources/sandcorp/deploy.sh가 같은 순서로 돌고 헬스 실패 시 자동 롤백한다
# (값은 Jenkinsfile). 순서를 바꾸면 두 곳을 같이 본다. Run from a directory that holds ./.env (not in the repo).
#   1) get the image: NEWS_IMAGE set (ghcr.io/sandcorporation/sandcorp-news:<sha>, built off-host, #142)
#      -> docker pull only, never build on oracle; NEWS_IMAGE unset (make prod-local) -> build locally
#   2) wait until the database (OCI MySQL HeatWave, outside compose) answers
#   3) migrate in a one-off container  4) replace web and scheduler
#   5) wait for the health check (it round-trips to the database)
# Migrations run while the old web still serves, so destructive schema changes take two deploys.
set -euo pipefail

if [ ! -f .env ]; then
  echo "워크스페이스에 .env가 없다. .env.example을 보고 만들어 두어야 한다." >&2
  exit 1
fi

compose() { docker compose "$@"; }

# sandcorp-authkit은 private 저장소다(빌드할 때만 필요). Jenkins는 GIT_SSH_KEY(키 파일 경로)를 주고, 로컬은 개발자의 ssh-agent를 쓴다.
if [ -z "${NEWS_IMAGE:-}" ] && [ -n "${GIT_SSH_KEY:-}" ]; then
  eval "$(ssh-agent -s)" >/dev/null
  trap 'ssh-agent -k >/dev/null 2>&1 || true' EXIT
  ssh-add "$GIT_SSH_KEY" 2>/dev/null
fi

wait_healthy() {
  local svc=$1
  for i in $(seq 1 90); do
    local id status
    id=$(compose ps -q "$svc")
    status=$(docker inspect -f '{{.State.Health.Status}}' "$id" 2>/dev/null || echo starting)
    if [ "$status" = "healthy" ]; then
      echo "$svc healthy (${i}회째)"
      return 0
    fi
    sleep 2
  done
  echo "$svc가 healthy가 되지 않았다" >&2
  compose logs --tail 80 "$svc" >&2
  return 1
}

if [ -n "${NEWS_IMAGE:-}" ]; then
  # ghcr 읽기 로그인은 oracle 호스트의 docker 설정에 있다(sandcorp-flow 와 같다). 빌드하지 않는다.
  echo "== 이미지 pull ($NEWS_IMAGE)"
  docker pull "$NEWS_IMAGE"
  BUILD_FLAG=--no-build
else
  echo "== 이미지 빌드"
  compose build --ssh default web
  BUILD_FLAG=
fi

# 운영 DB는 compose 밖(OCI MySQL HeatWave)이다. mysql 서비스는 make prod-local 덮어쓰기에만 있다.
if compose config --services | grep -qx mysql; then
  echo "== 로컬 MySQL (prod-local, 이미 떠 있으면 그대로)"
  compose up -d --no-recreate mysql
  wait_healthy mysql
fi

echo "== DB 접속 대기"
compose run --rm --no-deps web python manage.py wait_for_db --timeout 120

echo "== migrate"
compose run --rm --no-deps web python manage.py migrate --noinput

echo "== web 교체"
# --force-recreate: 새로 빌드한 이미지라도 compose가 "Running"으로 두고 넘어가는 경우가 있었다.
compose up -d --no-deps $BUILD_FLAG --force-recreate web
wait_healthy web

echo "== scheduler 교체 (같은 이미지)"
compose up -d --no-deps $BUILD_FLAG --force-recreate scheduler

echo "== 헬스 확인"
compose exec -T web python -c "
import os, urllib.request as u
host = os.environ.get('DJANGO_ALLOWED_HOSTS', 'localhost').split(',')[0].strip()
print(u.urlopen(u.Request('http://127.0.0.1:8000/api/health', headers={'Host': host}), timeout=5).read().decode())
"
compose ps
if [ -n "${NEWS_IMAGE:-}" ]; then
  # pull 한 옛 sha 이미지는 dangling 이 아니라 prune 에 안 걸린다 — 최신 3개만 남긴다(롤백은 다시 pull).
  docker image ls --format '{{.Repository}}:{{.Tag}}' "${NEWS_IMAGE%:*}" | tail -n +4 | xargs -r docker rmi >/dev/null 2>&1 || true
fi
docker image prune -f --filter label=com.docker.compose.project=${COMPOSE_PROJECT_NAME:-news} >/dev/null || true
