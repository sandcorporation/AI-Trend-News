// 운영 배포 — 운영 = production 브랜치 머리(#180). 이 잡은 production만 배포한다(잡 브랜치 지정자 */production).
//
//   main push → GitHub Actions release.yml: 검사(pr-check) → 이미지(image.yml, ubuntu-24.04-arm) → production fast-forward
//   production push → 여기: 굽지도 검사하지도 않는다. ghcr.io/sandcorporation/sandcorp-news:<sha>가 있는지 짧게(5분) 보고
//                     oracle에서 pull → DB 대기 → migrate → web 교체 → 헬스 → scheduler 교체(헬스 실패면 직전 성공 이미지로 자동 롤백)
//
// 파이프라인은 Jenkins 공용 라이브러리 sandcorp(sandcorporation/jenkins-shared)의 sandcorpDeploy — README 「승격 브랜치에서 배포」.
// Jenkins는 production에서 Actions가 GHCR에 올린 이미지를 배포한다. CI·검사·이미지 빌드는 GitHub Actions만 담당한다.
// 이 저장소가 넘기는 배포 값과 롤백 절차는 docs/ops/offhost-build.md.
@Library('sandcorp') _

sandcorpDeploy(
  name: 'Foresight',
  deployFrom: 'production',
  image: 'ghcr.io/sandcorporation/sandcorp-news',
  imageVar: 'NEWS_IMAGE',
  imageWaitHint: 'https://github.com/sandcorporation/AI-Trend-News/actions/workflows/release.yml',
  sourceUrl: 'https://github.com/sandcorporation/AI-Trend-News',
  composeProject: 'news',
  composeFiles: ['docker-compose.prod.yml', 'docker-compose.oracle.yml'],
  preServices: ['mysql'],               // 로컬 확인용 compose에만 있다. 운영 DB(HeatWave)는 compose 밖
  dbCheck: 'python manage.py wait_for_db --timeout 20',
  migrate: 'python manage.py migrate --noinput',
  extraServices: ['scheduler'],
  healthCmd: '''python -c "import os, urllib.request as u; host = os.environ.get('DJANGO_ALLOWED_HOSTS', 'localhost').split(',')[0].strip(); print(u.urlopen(u.Request('http://127.0.0.1:8000/api/health', headers={'Host': host}), timeout=5).read().decode())"''',
)
