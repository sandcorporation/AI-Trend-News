// main 배포 — 파이프라인은 Jenkins 공용 라이브러리 sandcorp(sandcorporation/jenkins-shared)의 sandcorpDeploy에 있다.
// 여기에는 이 저장소가 넘기는 값만 둔다. 동작(작업 브랜치 push 무시·이미 배포한 sha 건너뜀·배포 전 검사·mac 우선 빌드·
// oracle pull 배포·헬스 실패 시 자동 롤백·디스코드 알림)과 인자 목록은 jenkins-shared README, 이 저장소 사정은 docs/ops/offhost-build.md.
@Library('sandcorp') _

sandcorpDeploy(
  name: 'Foresight',
  image: 'ghcr.io/sandcorporation/sandcorp-news',
  imageVar: 'NEWS_IMAGE',
  sourceUrl: 'https://github.com/sandcorporation/AI-Trend-News',
  buildSsh: true,                       // sandcorp-authkit(비공개 git 의존성)
  check: 'bash scripts/ci/check.sh',    // make check를 도커 안에서. 실패하면 굽지도 배포하지도 않는다
  composeProject: 'news',
  composeFiles: ['docker-compose.prod.yml', 'docker-compose.oracle.yml'],
  preServices: ['mysql'],               // 로컬 확인용 compose에만 있다. 운영 DB(HeatWave)는 compose 밖
  dbCheck: 'python manage.py wait_for_db --timeout 20',
  migrate: 'python manage.py migrate --noinput',
  extraServices: ['scheduler'],
  healthCmd: '''python -c "import os, urllib.request as u; host = os.environ.get('DJANGO_ALLOWED_HOSTS', 'localhost').split(',')[0].strip(); print(u.urlopen(u.Request('http://127.0.0.1:8000/api/health', headers={'Host': host}), timeout=5).read().decode())"''',
)
