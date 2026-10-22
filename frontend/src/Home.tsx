import { Layout } from "./Layout";
import type { CardData, HomeData } from "./types";

export function Card({ card }: { card: CardData }) {
  return (
    <article className="card">
      {card.image_url && <img className="card-image" src={card.image_url} alt="" loading="lazy" />}
      <div className="card-body">
        <p className="meta">
          <span className="chip">{card.category.name}</span> <span>{card.published_label}</span>
        </p>
        <h3>
          <a href={card.url}>{card.title}</a>
        </h3>
        <p className="summary">{card.summary}</p>
      </div>
    </article>
  );
}

function pageHref(category: string, page: number): string {
  const params = new URLSearchParams();
  if (category) params.set("category", category);
  params.set("page", String(page));
  return `?${params.toString()}`;
}

export function Home({ data }: { data: HomeData }) {
  return (
    <Layout site={data.site}>
      <nav className="tabs" aria-label="분야">
        <a href="/" aria-current={data.category ? undefined : "page"}>
          전체
        </a>
        {data.categories.map((c) => (
          <a
            key={c.slug}
            href={`/?category=${c.slug}`}
            aria-current={c.slug === data.category ? "page" : undefined}
          >
            {c.name}
          </a>
        ))}
      </nav>
      {data.featured.length > 0 && (
        <section className="featured" aria-labelledby="featured-h">
          <h2 id="featured-h">추천</h2>
          {data.featured.map((card) => (
            <Card key={card.slug} card={card} />
          ))}
        </section>
      )}
      <section aria-labelledby="latest-h">
        <h2 id="latest-h">최신 기사</h2>
        {data.articles.length === 0 && <p className="empty">아직 게시된 기사가 없습니다.</p>}
        {data.articles.map((card) => (
          <Card key={card.slug} card={card} />
        ))}
      </section>
      {data.num_pages > 1 && (
        <nav className="pager" aria-label="페이지">
          {data.page > 1 && (
            <a rel="prev" href={pageHref(data.category, data.page - 1)}>
              이전
            </a>
          )}
          <span>
            {data.page} / {data.num_pages}
          </span>
          {data.page < data.num_pages && (
            <a rel="next" href={pageHref(data.category, data.page + 1)}>
              다음
            </a>
          )}
        </nav>
      )}
    </Layout>
  );
}
