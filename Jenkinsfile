// main에 푸시하면 oracle(Jenkins 에이전트, arm64)에 배포한다.
//
// .env는 여기서 만들지 않는다. 워크스페이스에 놓아 둔 것을 compose가 ./.env로 읽는다
// (oracle 호스트의 /var/jenkins_home/workspace/news/.env). checkout은 추적하지 않는 파일을 지우지 않는다.
//
// oracle의 80·443은 NPMplus가 쓴다. web은 docker 브리지 게이트웨이 172.17.0.1:18092에만 묶는다.
// NPMplus 프록시 호스트: news.sandcorp.co.kr → http://172.17.0.1:18092, Let's Encrypt.
//
// 시험은 로컬에서만 돌린다(CI 없음). 이 잡은 빌드·마이그레이션·교체·헬스 확인만 한다.

pipeline {
  agent { label 'oracle' }

  options {
    disableConcurrentBuilds()
    timeout(time: 30, unit: 'MINUTES')
    timestamps()
  }

  environment {
    COMPOSE_PROJECT_NAME = 'news'
    COMPOSE_FILE = 'docker-compose.prod.yml:docker-compose.oracle.yml'
    DOCKER_BUILDKIT = '1'
  }

  triggers {
    githubPush()
  }

  stages {
    stage('체크아웃') {
      steps {
        checkout scm
        sh 'git log --oneline -1'
      }
    }

    stage('빌드·배포') {
      steps {
        // sandcorp-authkit(private 저장소)을 이미지 빌드에서 SSH로 받는다. 키 경로·내용은 로그에 찍지 않는다.
        withCredentials([sshUserPrivateKey(credentialsId: 'github-farrar142', keyFileVariable: 'GIT_SSH_KEY')]) {
          sh 'bash deploy/deploy.sh'
        }
      }
    }
  }

  post {
    // 빌드가 끝나면 디스코드로 결과 한 줄을 보낸다. 배포는 사람이 화면을 보지 않을 때
    // 도는 일이라 끝났다는 사실(특히 실패)이 닿을 자리가 없었다. 모든 Sandcorp 제품이
    // 같은 웹훅을 쓰고 메시지의 [Foresight] 표식으로 제품을 구분한다.
    //
    // 웹훅 주소는 저장소에 두지 않는다 — Jenkins 자격증명(Secret text)
    // `discord-deploy-webhook` 을 이름으로만 부른다. 알림이 실패해도 빌드 결과를
    // 뒤집지 않는다(`catchError` 와 curl 끝의 `|| true`).
    always {
      catchError(buildResult: null, stageResult: null, message: '디스코드 알림 실패 — 배포 결과와 무관하다') {
        withCredentials([string(credentialsId: 'discord-deploy-webhook', variable: 'DISCORD_WEBHOOK')]) {
          script {
            def result = currentBuild.currentResult
            def mark = result == 'SUCCESS' ? '✅' : (result == 'ABORTED' ? '⚪' : '❌')
            def sha = (env.GIT_COMMIT ?: '').take(12)
            def took = currentBuild.durationString.replace(' and counting', '')
            def text = "${mark} [Foresight] 배포 ${result} — #${env.BUILD_NUMBER}" +
              (sha ? " (${sha})" : '') + " · ${took} · ${env.BUILD_URL}"
            // 본문은 JSON 이라 따옴표·역슬래시를 셸이 다시 해석하지 않도록 파일로 넘긴다.
            writeFile file: 'discord-payload.json', text: groovy.json.JsonOutput.toJson([content: text])
            sh 'curl -sS -X POST -H "Content-Type: application/json" --data @discord-payload.json "$DISCORD_WEBHOOK" > /dev/null || true'
          }
        }
      }
    }
    failure {
      echo '배포 실패 — deploy/deploy.sh 출력과 compose 로그를 보라.'
    }
  }
}
