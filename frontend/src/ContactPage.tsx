import { useEffect, useRef } from "react";
import { restoreFocus, type ContactDraft } from "./contactDraft";
import { Layout } from "./Layout";
import type { ContactPageData } from "./types";

/** 문의 폼. JS 없이도 되는 일반 POST 폼이다(오류는 같은 페이지, 성공은 303 뒤 `?sent=1`). */
export function ContactPage({
  data,
  draft = null,
}: {
  data: ContactPageData;
  draft?: ContactDraft | null;
}) {
  // JS가 늦게 붙으면 서버 HTML 폼에 이미 친 값이 초깃값이다.
  const form = { ...data.form, ...draft?.values };
  const summary = useRef<HTMLDivElement>(null);
  const section = useRef<HTMLElement>(null);
  const initialFocus = useRef(draft?.focus);
  // 마운트 때 한 번: 초점이 있던 요소가 있으면 거기, 없으면 400 화면의 오류 요약이다.
  useEffect(() => {
    if (section.current && initialFocus.current)
      restoreFocus(section.current, initialFocus.current);
    else summary.current?.focus();
  }, []);
  return (
    <Layout site={data.site}>
      <section ref={section} className="contact" data-slot="contact">
        <h1>문의</h1>
        {data.state === "done" ? (
          <>
            <p className="notice ok" role="status">
              접수했습니다. 남겨 주신 연락처로 답변드리겠습니다.
            </p>
            <p>
              <a className="btn" href="/">
                메인으로
              </a>
            </p>
          </>
        ) : (
          <>
            <p className="lead">문의나 제안을 남겨 주세요.</p>
            {data.state === "limited" && (
              <p className="notice err" role="alert">
                요청이 너무 많습니다. 잠시 뒤에 다시 시도해 주세요.
              </p>
            )}
            {data.error_summary.length > 0 && (
              <div
                ref={summary}
                className="notice err error-summary"
                role="alert"
                tabIndex={-1}
              >
                <p>입력한 내용을 확인해 주세요.</p>
                <ul>
                  {data.error_summary.map((e) => (
                    <li key={e.href}>
                      <a href={e.href}>{e.text}</a>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <form
              method="post"
              action="/contact"
              className="contact-form"
            >
              <div className="hp" aria-hidden="true">
                <label>
                  웹사이트{" "}
                  <input
                    type="text"
                    name="website"
                    tabIndex={-1}
                    autoComplete="off"
                  />
                </label>
              </div>
              <fieldset className="kind">
                <legend>종류</legend>
                {data.kinds.map((k, i) => (
                  <label key={k}>
                    <input
                      type="radio"
                      name="kind"
                      value={k}
                      id={i === 0 ? "contact-kind" : undefined}
                      {...errorProps("kind", form.errors.kind)}
                      defaultChecked={k === form.kind}
                    />{" "}
                    {k}
                  </label>
                ))}
                <FieldError field="kind" text={form.errors.kind} />
              </fieldset>
              <label>
                이름{" "}
                <input
                  name="name"
                  id="contact-name"
                  {...errorProps("name", form.errors.name)}
                  required
                  maxLength={50}
                  autoComplete="name"
                  defaultValue={form.name}
                />
              </label>
              <FieldError field="name" text={form.errors.name} />
              <label>
                회사 (선택){" "}
                <input
                  name="company"
                  id="contact-company"
                  {...errorProps("company", form.errors.company)}
                  maxLength={100}
                  autoComplete="organization"
                  defaultValue={form.company}
                />
              </label>
              <FieldError field="company" text={form.errors.company} />
              <label>
                연락처 (이메일 또는 전화){" "}
                <input
                  name="contact"
                  id="contact-contact"
                  {...errorProps("contact", form.errors.contact)}
                  required
                  maxLength={200}
                  defaultValue={form.contact}
                />
              </label>
              <FieldError field="contact" text={form.errors.contact} />
              <label>
                내용{" "}
                <textarea
                  name="message"
                  id="contact-message"
                  {...errorProps("message", form.errors.message)}
                  required
                  rows={6}
                  maxLength={5000}
                  defaultValue={form.message}
                />
              </label>
              <FieldError field="message" text={form.errors.message} />
              <button className="btn primary" type="submit">
                보내기
              </button>
            </form>
          </>
        )}
      </section>
    </Layout>
  );
}

function errorProps(field: string, text?: string) {
  return text
    ? {
        "aria-invalid": true,
        "aria-describedby": `contact-${field}-error`,
      }
    : {};
}

function FieldError({ field, text }: { field: string; text?: string }) {
  return text ? (
    <p className="field-error" id={`contact-${field}-error`}>
      {text}
    </p>
  ) : null;
}
