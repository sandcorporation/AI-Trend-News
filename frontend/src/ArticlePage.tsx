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
        {/* #12: article-bottom CTA (Sandcorp inflow) goes in this slot. */}
        <section className="cta" data-slot="article-cta" />
      </article>
    </Layout>
  );
}
