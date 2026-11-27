import { Fragment } from "react";
import { Card } from "./Home";
import { Layout } from "./Layout";
import { ShareFragment } from "./Share";
import type { ArticlePageData, CommentaryData } from "./types";

export function ArticlePage({ data }: { data: ArticlePageData }) {
  const a = data.article;
  return (
    <Layout site={data.site}>
      <article className="detail">
        <p className="meta">
          <a
            className={`chip tone-${a.tone}`}
            href={`/?category=${a.category.slug}`}
          >
            {a.category.name}
          </a>{" "}
          · <time dateTime={a.published_at}>{a.published_label}</time> ·{" "}
          <span>출처 {a.items.length}건</span>
        </p>
        <h1>{a.title}</h1>
        <p className="summary">{a.summary}</p>
        {a.image_url && <img className="hero" src={a.image_url} alt="" />}
        {a.tags.length > 0 && (
          <ul className="tags">
            {a.tags.map((t) => (
              <li key={t}>#{t}</li>
            ))}
          </ul>
        )}
        <section className="sources-block" aria-labelledby="sources-h">
          <h2 id="sources-h">원문</h2>
          <ul className="sources">
            {a.items.map((i) => (
              <li key={i.url}>
                <a href={i.url} target="_blank" rel="noopener">
                  {i.title}
                </a>{" "}
                <span className="source-name">
                  {i.source}
                  {i.published_label && ` · ${i.published_label}`}
                </span>
              </li>
            ))}
          </ul>
        </section>
        {a.commentary && <Commentary c={a.commentary} />}
        <ShareFragment share={data.share} title={a.title} text={a.summary} />
        {data.related.length > 0 && (
          <section
            className="related"
            data-slot="related"
            aria-labelledby="related-h"
          >
            <h2 id="related-h">{data.related_title}</h2>
            <div className="grid">
              {data.related.map((card) => (
                <Card key={card.slug} card={card} site={data.site} />
              ))}
            </div>
          </section>
        )}
        <section className="cta" data-slot="article-cta">
          <p>{data.cta.text}</p>
          <p className="cta-actions">
            <a className="btn primary" href={data.cta.portfolio_url}>
              {data.cta.portfolio_label}
            </a>
            <a className="btn" href={data.cta.contact_url}>
              {data.cta.contact_label}
            </a>
          </p>
        </section>
      </article>
    </Layout>
  );
}

/** 서버 템플릿의 `linebreaks`와 같게: 빈 줄로 문단을 나누고 문단 안 줄바꿈은 <br>. */
function Prose({ text }: { text: string }) {
  const paragraphs = text
    .replace(/\r\n?/g, "\n")
    .trim()
    .split(/\n{2,}/);
  return (
    <div className="sc-prose">
      {paragraphs.map((para, i) => (
        <p key={i}>
          {para.split("\n").map((line, j) => (
            <Fragment key={j}>
              {j > 0 && <br />}
              {line}
            </Fragment>
          ))}
        </p>
      ))}
    </div>
  );
}

function Commentary({ c }: { c: CommentaryData }) {
  return (
    <section
      className="commentary"
      data-slot="commentary"
      aria-labelledby="commentary-h"
    >
      <header className="commentary-head">
        <h2 id="commentary-h">
          <span className="badge-ai">AI 분석</span>
        </h2>
        <p className="commentary-note">
          수집된 보도와 일반적인 배경 지식을 바탕으로 편집자 AI가 쓴
          분석입니다. 사실 확인은 출처를 참고하세요.
        </p>
      </header>
      <h3>배경</h3>
      <Prose text={c.background} />
      <h3>의미</h3>
      <Prose text={c.significance} />
      <h3>전망</h3>
      <div className="outlook">
        <div className="outlook-col outlook-bull">
          <h4>낙관</h4>
          <Prose text={c.outlook_bull} />
        </div>
        <div className="outlook-col outlook-bear">
          <h4>회의</h4>
          <Prose text={c.outlook_bear} />
        </div>
      </div>
      {c.watchpoints.length > 0 && (
        <>
          <h3>지켜볼 신호</h3>
          <ul className="watchpoints">
            {c.watchpoints.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </>
      )}
      <blockquote className="opinion">
        <p>{c.opinion}</p>
      </blockquote>
      {c.predictions.length > 0 && (
        <>
          <h3>예측</h3>
          <ul className="predictions">
            {c.predictions.map((p) => (
              <li key={p.claim}>
                <span className="horizon">{p.horizon_label}</span> {p.claim}{" "}
                <span className="basis">근거: {p.basis}</span>{" "}
                <span className="due">기한 {p.due_label}</span>
                {p.verdict && (
                  <>
                    {" "}
                    <span
                      className={`verdict verdict-${p.verdict}`}
                      data-slot="verdict"
                    >
                      {p.verdict_label}
                    </span>
                  </>
                )}
              </li>
            ))}
          </ul>
          <p className="predictions-more">
            <a className="more" href="/predictions">
              전망 기록표 →
            </a>
          </p>
        </>
      )}
    </section>
  );
}
