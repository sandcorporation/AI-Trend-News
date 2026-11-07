import type { ErrorEvent } from "@sentry/react";
import { describe, expect, it, vi } from "vitest";
import { buildSentryOptions, initSentry, readSentryConfig } from "./observability";

const JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJvd25lciJ9.c2lnbmF0dXJlLXZhbHVl";

function docWith(json: string | null): Document {
  const doc = document.implementation.createHTMLDocument("t");
  if (json !== null) {
    const node = doc.createElement("script");
    node.type = "application/json";
    node.id = "sentry-config";
    node.textContent = json;
    doc.head.appendChild(node);
  }
  return doc;
}

describe("initSentry", () => {
  it("does not init without the shell config", () => {
    const init = vi.fn();
    expect(initSentry(docWith(null), init)).toBe(false);
    expect(initSentry(docWith('{"dsn": "", "release": ""}'), init)).toBe(false);
    expect(initSentry(docWith("not json"), init)).toBe(false);
    expect(init).not.toHaveBeenCalled();
  });

  it("inits with production options when the DSN is set", () => {
    const init = vi.fn();
    const release = "c".repeat(40);
    const json = JSON.stringify({ dsn: "https://public@example.invalid/2", release });
    expect(initSentry(docWith(json), init)).toBe(true);
    expect(init).toHaveBeenCalledWith(
      expect.objectContaining({
        dsn: "https://public@example.invalid/2",
        release,
        environment: "production",
        dataCollection: { userInfo: false, cookies: false, httpBodies: [] },
        tracesSampleRate: 0,
      }),
    );
  });

  it("reads an empty release as undefined", () => {
    const config = readSentryConfig(docWith('{"dsn": "https://public@example.invalid/2"}'));
    expect(config).toEqual({ dsn: "https://public@example.invalid/2", release: "" });
    if (!config) throw new Error("config");
    expect(buildSentryOptions(config).release).toBeUndefined();
  });
});

describe("beforeSend", () => {
  it("drops cookies, body and credential headers and masks tokens", () => {
    const options = buildSentryOptions({ dsn: "https://public@example.invalid/2", release: "" });
    const event = {
      type: undefined,
      message: `실패 ${JWT}`,
      request: {
        url: `https://news.sandcorp.co.kr/a/x?token=abc&page=2#${JWT}`,
        cookies: { sessionid: "abc" },
        data: "문의 본문",
        headers: { Cookie: "sessionid=abc", Authorization: `Bearer ${JWT}`, Accept: "*/*" },
        query_string: "code=secret1&page=2",
      },
    } as unknown as ErrorEvent;

    const beforeSend = options.beforeSend;
    if (!beforeSend) throw new Error("beforeSend");
    const sent = beforeSend(event, {}) as ErrorEvent;

    expect(sent.request?.cookies).toBeUndefined();
    expect(sent.request?.data).toBeUndefined();
    expect(sent.request?.headers).toEqual({ Accept: "*/*" });
    expect(sent.request?.query_string).toBe("code=[redacted]&page=2");
    expect(sent.request?.url).toBe("https://news.sandcorp.co.kr/a/x?token=[redacted]&page=2#[redacted]");
    expect(JSON.stringify(sent)).not.toContain(JWT);
  });
});
