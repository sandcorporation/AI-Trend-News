// main.tsx가 맨 먼저 import한다 — ES import는 끌어올려지므로 다른 모듈이 평가되기 전에 SDK를 켜려면
// 부수 효과를 이 모듈 하나에 둔다(issue 168).
import { initSentry } from "./observability";

initSentry();
