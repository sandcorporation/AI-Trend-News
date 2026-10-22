import { useState } from "react";
import { Layout } from "./Layout";
import type { ArticlePageData } from "./types";

export function ArticlePage({ data }: { data: ArticlePageData }) {
  const a = data.article;
  return (
    <Layout site={data.site}>
      <article className="detail">
        <p className="meta">
          <a className="chip" href={`/?category=${a.category.slug}`}>
            {a.category.name}
          </a>{" "}
          <time dateTime={a.published_at}>{a.published_label}</time>
        </p>
        <h1>{a.title}</h1>
        {a.image_url && <img className="hero" src={a.image_url} alt="" />}
        <p className="summary">{a.summary}</p>
        {a.tags.length > 0 && (
          <ul className="tags">
            {a.tags.map((t) => (
              <li key={t}>#{t}</li>
            ))}
          </ul>
        )}
        <section aria-labelledby="sources-h">
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
        <div className="share" data-slot="share">
          <a className="btn" href={data.share.x_url} target="_blank" rel="noopener">
            X에 공유
          </a>
          <CopyLinkButton url={data.share.url} />
        </div>
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

/** Copies the article URL; if the clipboard API is unavailable or refuses, shows the URL to copy by hand. */
function CopyLinkButton({ url }: { url: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      setState("copied");
    } catch {
      setState("failed");
    }
  };
  return (
    <>
      <button className="btn" type="button" onClick={copy}>
        {state === "copied" ? "복사됨" : "링크 복사"}
      </button>
      {state === "failed" && (
        <input
          className="copy-fallback"
          readOnly
          value={url}
          aria-label="기사 주소"
          onFocus={(e) => e.currentTarget.select()}
          ref={(el) => el?.select()}
        />
      )}
    </>
  );
}
