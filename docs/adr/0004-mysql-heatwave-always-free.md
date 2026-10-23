---
status: accepted
---

# DB는 OCI MySQL HeatWave Always Free(MySQL 단일 백엔드)

운영 DB를 compose 안 Postgres 컨테이너에서 OCI MySQL HeatWave Always Free 인스턴스(MySQL 26.10, 운영 VM과 같은 VCN의 사설 IP)로 옮기고, 저장소는 MySQL 하나만 지원한다(로컬 개발·시험도 `mysql:26.10.0` 컨테이너). 공유 oracle 호스트에서 Postgres 컨테이너 세 개(news·portfolio·auth)가 쓰던 CPU·메모리·백업 부담을 무료 관리형 DB 하나로 넘기려는 것이고, Oracle ADB와 함께 검증한 결과(2026-10-23) MySQL은 코드 변경 없이 시험 228건이 모두 통과했으며 Django가 1순위로 지원하는 엔진이라 택했다. 엔진을 환경 변수로 고르는 분기는 두지 않는다 — 시험이 운영과 다른 엔진으로 도는 틈을 남기지 않기 위해서다.

## 결과

- DB는 `utf8mb4` / `utf8mb4_0900_as_cs`로 만든다. 서버 기본 `utf8mb4_0900_ai_ci`는 대소문자·악센트를 무시해 `/a/<대문자 slug>`가 200이 되고, 태그 `get_or_create`가 다른 대소문자의 태그를 돌려주며, 대소문자만 바뀐 전망 claim의 분석 upsert가 유일 제약에 걸린다. `tests/test_db_collation.py`가 이를 고정한다.
- 드라이버는 `mysqlclient`(C 확장). 리눅스 휠이 없어 Dockerfile 빌드 단계에서 컴파일하고 런타임에는 `libmariadb3`만 둔다. 개발 머신에는 `pkg-config default-libmysqlclient-dev`가 필요하다.
- 트랜잭션 격리는 Django 기본(세션 READ COMMITTED)을 쓴다. 서버 기본 REPEATABLE READ는 검증에서 갭 락 교착이 났다.
- DB가 compose 밖이라 배포는 DB 컨테이너를 띄우지 않고 `wait_for_db`로 접속만 기다린다. 백업은 HeatWave 쪽 기능을 쓴다.
- 요청당 쿼리 수가 원격 DB 왕복 지연에 곱해진다. 홈 쿼리 상한(11개)을 계속 지킨다.
- `dumpdata`/`loaddata`는 datetime을 밀리초까지만 옮긴다(이전 때 한 번 생기는 손실).
