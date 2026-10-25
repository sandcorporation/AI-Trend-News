#!/usr/bin/env bash
# 기본 og:image(backend/core/static/core/og-default.png, 1200x630)를 og-default.svg에서 다시 만든다.
# 필요: google-chrome(헤드리스), frontend/node_modules(pretendard). 결과 PNG는 커밋한다.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
font="$root/frontend/node_modules/pretendard/dist/web/static/woff2"
out="$root/backend/core/static/core/og-default.png"
tmp="$(mktemp -d)"
trap 'rm -r "$tmp"' EXIT
{
  echo "<!doctype html><meta charset=utf-8><style>"
  echo "@font-face{font-family:Pretendard;font-weight:700;src:url('file://$font/Pretendard-Bold.woff2')}"
  echo "@font-face{font-family:Pretendard;font-weight:600;src:url('file://$font/Pretendard-SemiBold.woff2')}"
  echo "html,body{margin:0;background:#fff}svg{display:block}</style>"
  cat "$here/og-default.svg"
} > "$tmp/og.html"
google-chrome --headless=new --no-sandbox --hide-scrollbars --force-device-scale-factor=1 \
  --window-size=1200,630 --virtual-time-budget=3000 --screenshot="$out" "file://$tmp/og.html" >/dev/null 2>&1
echo "$out"
