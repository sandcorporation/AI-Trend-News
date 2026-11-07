// 오류 수집(GlitchTip, issue 168). 본보기는 codemit `frontend/lib/observability/sentry-options.ts`.
//
// DSN·release는 Vite 빌드에 박지 않는다 — 이미지 하나를 커밋 sha로 그대로 배포하므로 서버 셸
// (`core/shell.py::sentry_public_config`)이 실행 env 값을 `<script id="sentry-config">` JSON으로
// 넘긴다. 그 태그가 없으면(DSN 비었음: dev·시험) SDK를 켜지 않는다.
//
// SDK v11은 `sendDefaultPii` 대신 `dataCollection`으로 사용자 정보·쿠키·본문 수집을 끈다.
// 옵션 하나를 믿지 않고 `beforeSend`에서 한 번 더 직접 지운다.
// 성능 추적·리플레이는 켜지 않는다(오류만 보낸다).
import * as Sentry from "@sentry/react";
import type { ErrorEvent } from "@sentry/react";

/** 이름으로 지우는 요청 헤더(소문자). 백엔드 `core.observability.CREDENTIAL_HEADERS`와 같다. */
const CREDENTIAL_HEADERS = new Set([
  "cookie",
  "set-cookie",
  "authorization",
  "proxy-authorization",
  "x-api-key",
]);

/** 값을 통째로 지우는 쿼리 키(소문자 부분 일치). */
const SECRET_QUERY_KEYS = ["token", "code", "secret", "key", "password", "session", "state"];

const TOKEN_PATTERNS = [
  /eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*/g,
  /bearer\s+[A-Za-z0-9._~+/=-]{8,}/gi,
  /\bsk-[A-Za-z0-9_-]{16,}/g,
  /\b[A-Fa-f0-9]{40,}\b/g,
];

export const REDACTED = "[redacted]";

export function scrubText(text: string): string {
  return TOKEN_PATTERNS.reduce((acc, pattern) => acc.replace(pattern, REDACTED), text);
}

function scrubQuery(query: string): string {
  return query
    .split("&")
    .map((pair) => {
      const at = pair.indexOf("=");
      if (at < 0) return pair;
      const name = pair.slice(0, at);
      const secret = SECRET_QUERY_KEYS.some((key) => name.toLowerCase().includes(key));
      return `${name}=${secret ? REDACTED : scrubText(pair.slice(at + 1))}`;
    })
    .join("&");
}

type ScrubbableRequest = {
  url?: string;
  cookies?: unknown;
  data?: unknown;
  headers?: Record<string, string>;
  query_string?: unknown;
};

type ScrubbableEvent = { request?: ScrubbableRequest; message?: string };

/** 요청의 쿠키·본문·자격증명 헤더를 지우고, URL·쿼리·헤더·메시지의 토큰 모양 값을 가린다. */
export function scrubSensitiveRequestData<T extends ScrubbableEvent>(event: T): T {
  const request = event.request;
  if (request) {
    delete request.cookies;
    delete request.data;
    if (request.headers) {
      request.headers = Object.fromEntries(
        Object.entries(request.headers)
          .filter(([name]) => !CREDENTIAL_HEADERS.has(name.toLowerCase()))
          .map(([name, value]) => [name, scrubText(String(value))]),
      );
    }
    if (typeof request.query_string === "string") {
      request.query_string = scrubQuery(request.query_string);
    }
    if (typeof request.url === "string") {
      const at = request.url.indexOf("?");
      request.url = scrubText(
        at < 0 ? request.url : `${request.url.slice(0, at)}?${scrubQuery(request.url.slice(at + 1))}`,
      );
    }
  }
  if (typeof event.message === "string") event.message = scrubText(event.message);
  return event;
}

export type SentryPublicConfig = { dsn: string; release: string };

/** 셸이 넘긴 공개 설정. 태그가 없거나 DSN이 비었거나 JSON이 깨졌으면 null. */
export function readSentryConfig(doc: Document = document): SentryPublicConfig | null {
  const node = doc.getElementById("sentry-config");
  if (!node?.textContent) return null;
  try {
    const parsed: unknown = JSON.parse(node.textContent);
    if (typeof parsed !== "object" || parsed === null) return null;
    const { dsn, release } = parsed as Record<string, unknown>;
    if (typeof dsn !== "string" || dsn.trim() === "") return null;
    return { dsn: dsn.trim(), release: typeof release === "string" ? release.trim() : "" };
  } catch {
    return null;
  }
}

export function buildSentryOptions(config: SentryPublicConfig): Sentry.BrowserOptions {
  return {
    dsn: config.dsn,
    release: config.release === "" ? undefined : config.release,
    environment: "production",
    // 백엔드 send_default_pii=False와 같은 뜻: 사용자 정보·쿠키·요청/응답 본문을 모으지 않는다.
    dataCollection: { userInfo: false, cookies: false, httpBodies: [] },
    tracesSampleRate: 0,
    beforeSend: (event: ErrorEvent) => scrubSensitiveRequestData(event as ScrubbableEvent & ErrorEvent),
  };
}

/** 공개 설정이 있을 때만 SDK를 켠다. 켰으면 true. `main.tsx`가 맨 처음 부른다. */
export function initSentry(
  doc: Document = document,
  init: (options: Sentry.BrowserOptions) => unknown = Sentry.init,
): boolean {
  const config = readSentryConfig(doc);
  if (!config) return false;
  init(buildSentryOptions(config));
  return true;
}
