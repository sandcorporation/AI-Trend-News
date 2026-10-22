import { Layout } from "./Layout";
import type { ContactPageData } from "./types";

/** 문의 폼. JS 없이도 되는 일반 POST 폼이다(서버가 결과를 같은 페이지로 다시 그린다). */
export function ContactPage({ data }: { data: ContactPageData }) {
  const { form } = data;
  return (
    <Layout site={data.site}>
      <section className="contact" data-slot="contact">
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
            <form method="post" action="/contact" className="contact-form">
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
                {data.kinds.map((k) => (
                  <label key={k}>
                    <input
                      type="radio"
                      name="kind"
                      value={k}
                      defaultChecked={k === form.kind}
                    />{" "}
                    {k}
                  </label>
                ))}
              </fieldset>
              <label>
                이름{" "}
                <input
                  name="name"
                  required
                  maxLength={50}
                  autoComplete="name"
                  defaultValue={form.name}
                />
              </label>
              <FieldError text={form.errors.name} />
              <label>
                회사 (선택){" "}
                <input
                  name="company"
                  maxLength={100}
                  autoComplete="organization"
                  defaultValue={form.company}
                />
              </label>
              <label>
                연락처 (이메일 또는 전화){" "}
                <input
                  name="contact"
                  required
                  maxLength={200}
                  defaultValue={form.contact}
                />
              </label>
              <FieldError text={form.errors.contact} />
              <label>
                내용{" "}
                <textarea
                  name="message"
                  required
                  rows={6}
                  maxLength={5000}
                  defaultValue={form.message}
                />
              </label>
              <FieldError text={form.errors.message} />
              <FieldError text={form.errors.kind} />
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

function FieldError({ text }: { text?: string }) {
  return text ? <p className="field-error">{text}</p> : null;
}
