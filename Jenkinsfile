// 운영 배포 — 운영 = production 브랜치 머리(#180). 이 잡은 production만 배포한다(잡 브랜치 지정자 */production).
//
//   main push → GitHub Actions release.yml: 검사(pr-check) → 이미지(image.yml, ubuntu-24.04-arm) → production fast-forward
//   production push → 여기: 굽지도 검사하지도 않는다. ghcr.io/sandcorporation/sandcorp-news:<sha>가 있는지 짧게(5분) 보고
//                     oracle에서 pull → DB 대기 → migrate → web 교체 → 헬스 → scheduler 교체(헬스 실패면 직전 성공 이미지로 자동 롤백)
//
// 파이프라인은 Jenkins 공용 라이브러리 sandcorp(sandcorporation/jenkins-shared)의 sandcorpDeploy — README 「승격 브랜치에서 배포」.
// 여기에는 이 저장소가 넘기는 값만 둔다. 이 저장소 사정은 docs/ops/offhost-build.md. 파라미터:
//   BUILD_ON=jenkins  비상 길: Actions가 못 구울 때 oracle 밖(macos, MAC_WAIT_SECONDS 안에 못 잡으면 home)에서
//                     check 뒤 굽고 배포한다. 이 길로 올린 sha는 production 머리 그대로다.
//   ROLLBACK_SHA      비상 되돌리기: 빌드 없이 그 sha의 이미지로 배포. production은 그대로라 다음 승격이 다시 앞으로 간다.
//                     정석은 main에 revert PR — docs/ops/offhost-build.md 「되돌리기」
@Library('sandcorp') _

sandcorpDeploy(
  name: 'Foresight',
  deployFrom: 'production',
  image: 'ghcr.io/sandcorporation/sandcorp-news',
  imageVar: 'NEWS_IMAGE',
  imageWaitHint: 'https://github.com/sandcorporation/AI-Trend-News/actions/workflows/release.yml',
  sourceUrl: 'https://github.com/sandcorporation/AI-Trend-News',
  buildSsh: true,                       // 비상 길(BUILD_ON=jenkins)의 sandcorp-authkit ssh 받기
  check: 'bash scripts/ci/check.sh',    // 비상 길에서만: make check를 도커 안에서. 실패하면 굽지도 배포하지도 않는다
  composeProject: 'news',
  composeFiles: ['docker-compose.prod.yml', 'docker-compose.oracle.yml'],
  preServices: ['mysql'],               // 로컬 확인용 compose에만 있다. 운영 DB(HeatWave)는 compose 밖
  dbCheck: 'python manage.py wait_for_db --timeout 20',
  migrate: 'python manage.py migrate --noinput',
  extraServices: ['scheduler'],
  healthCmd: '''python -c "import os, urllib.request as u; host = os.environ.get('DJANGO_ALLOWED_HOSTS', 'localhost').split(',')[0].strip(); print(u.urlopen(u.Request('http://127.0.0.1:8000/api/health', headers={'Host': host}), timeout=5).read().decode())"''',
)
