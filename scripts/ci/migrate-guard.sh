#!/usr/bin/env bash
# PR 에서 새로 생긴 Django migration 만 골라 migrate-guard.py 에 넘긴다(파괴적 연산이면 실패).
#
#   bash migrate-guard.sh <기준 rev>        예: origin/main. 기준..HEAD 에서 추가(A)된 */migrations/*.py
#
# python3(3.8+)이 있으면 그것으로, 없으면 python:3.13-slim 도커로 돌린다(git 은 노드에서).
# GUARD_PY: migrate-guard.py 경로(기본: 이 스크립트 옆).
set -euo pipefail
base="${1:?기준 rev 가 필요하다}"
guard="${GUARD_PY:-$(cd "$(dirname "$0")" && pwd)/migrate-guard.py}"

git rev-parse -q --verify "$base^{commit}" >/dev/null || { echo "migrate-guard: 기준 $base 를 찾지 못했다" >&2; exit 2; }

files=()
while IFS= read -r -d '' f; do
  case "$f" in
    */__init__.py) ;;
    migrations/*.py | */migrations/*.py) files+=("$f") ;;
  esac
done < <(git diff --name-only --diff-filter=A -z "$base" HEAD)

echo "migrate-guard: 기준 $(git rev-parse --short=12 "$base") .. HEAD $(git rev-parse --short=12 HEAD)"
if [ "${#files[@]}" -eq 0 ]; then
  echo "migrate-guard: 새 migration 없음"
  exit 0
fi

if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; sys.exit(sys.version_info < (3, 8))' 2>/dev/null; then
  exec python3 -I "$guard" "${files[@]}"
fi
echo "migrate-guard: python3(3.8+)이 없어 도커로 돌린다"
exec docker run --rm -u "$(id -u):$(id -g)" -v "$PWD:/w:ro" -v "$guard:/guard.py:ro" -w /w python:3.13-slim \
  python -I /guard.py "${files[@]}"
