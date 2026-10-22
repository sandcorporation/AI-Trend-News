---
status: accepted
---

# 문의는 portfolio가 소유하고, 이 사이트는 전달만 한다

뉴스 사이트에도 문의 폼을 두지만 문의 데이터의 원본과 Discord 알림은 sandcorp.co.kr(portfolio)에 있다. 뉴스 서버는 폼 제출을 받아 출처(site)와 종류(kind)를 붙여 portfolio 문의 API로 서버 간 전송하고, 전송에 실패한 건만 재시도용으로 잠시 보관한다. 사이트의 목적이 포트폴리오 유입이므로 문의가 한 곳에 모이는 것이 사이트별 관리보다 중요하고, 브라우저가 portfolio에 직접 POST하는 방식은 CORS와 실패 시 유실 문제가 있어 택하지 않았다.
