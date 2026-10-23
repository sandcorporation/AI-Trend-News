import { useState } from "react";
import { Layout } from "./Layout";
import type { CardData, HomeData, SectionData, SiteData } from "./types";

/** Image with a tone-coloured monogram behind it; a broken image just disappears. */
function Thumb({ card, site, eager }: { card: CardData; site: SiteData; eager?: boolean }) {
  const [broken, setBroken] = useState(false);
  return (
    <div className={`thumb tone-${card.tone}`} data-monogram={site.monogram}>
      <span className="monogram" aria-hidden="true">
        {site.monogram}
      </span>
      {card.image_url && !broken && (
        <img
          src={card.image_url}
          alt=""
          referrerPolicy="no-referrer"
          loading={eager ? "eager" : "lazy"}
          onError={() => setBroken(true)}
        />
      )}
    </div>
  );
}

function Meta({ card, chip = true }: { card: CardData; chip?: boolean }) {
  return (
    <p className="meta">
      {chip && <span className={`chip tone-${card.tone}`}>{card.category.name}</span>}{" "}
      <time dateTime={card.published_at}>{card.published_label}</time>
    </p>
  );
}

export function Card({ card, site }: { card: CardData; site: SiteData }) {
  return (
    <article className="card">
      <a className="card-thumb" href={card.url} tabIndex={-1} aria-hidden="true">
        <Thumb card={card} site={site} />
      </a>
      <div className="card-body">
        <Meta card={card} />
        <h3>
          <a href={card.url}>{card.title}</a>
        </h3>
      </div>
    </article>
  );
}

function Hero({ cards, site }: { cards: CardData[]; site: SiteData }) {
  return (
    <section className="home-hero" data-slot="hero" aria-labelledby="hero-h">
      <h2 id="hero-h" className="sr-only">
        추천
      </h2>
      {cards.map((card, i) => (
        <article key={card.slug} className={`hero-card ${i === 0 ? "hero-main" : "hero-side"}`}>
          <a className="hero-thumb" href={card.url} tabIndex={-1} aria-hidden="true">
            <Thumb card={card} site={site} eager={i === 0} />
          </a>
          <div className="hero-text">
            <Meta card={card} />
            <h3>
              <a href={card.url}>{card.title}</a>
            </h3>
            {i === 0 && <p className="summary">{card.summary}</p>}
          </div>
        </article>
      ))}
    </section>
  );
}

function Section({ section, site }: { section: SectionData; site: SiteData }) {
  const [first, ...rest] = section.articles;
  const id = `sec-${section.category.slug}`;
  return (
    <section className="cat-section" aria-labelledby={id}>
      <div className="section-head">
        <h2 id={id}>
          <span className={`dot tone-${first?.tone ?? "ai"}`}></span>
          {section.category.name}
        </h2>
        <a className="more" href={section.more_url}>
          더보기 →
        </a>
      </div>
      <div className="section-body">
        {first && (
          <article className="card card-feature">
            <a className="card-thumb" href={first.url} tabIndex={-1} aria-hidden="true">
              <Thumb card={first} site={site} />
            </a>
            <div className="card-body">
              <Meta card={first} chip={false} />
              <h3>
                <a href={first.url}>{first.title}</a>
              </h3>
              <p className="summary">{first.summary}</p>
            </div>
          </article>
        )}
        <ul className="title-list">
          {rest.map((card) => (
            <li key={card.slug}>
              <a href={card.url}>{card.title}</a>{" "}
              <time dateTime={card.published_at}>{card.published_label}</time>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

function Sidebar({ data }: { data: HomeData }) {
  const s = data.sidebar;
  return (
    <aside className="sidebar" aria-label="사이드바">
      {data.most_read.length > 0 && (
        <section className="side-block" aria-labelledby="most-read-h">
          <h2 id="most-read-h">{s.most_read_title}</h2>
          <ol className="ranked">
            {data.most_read.map((card) => (
              <li key={card.slug}>
                <a href={card.url}>{card.title}</a>
              </li>
            ))}
          </ol>
        </section>
      )}
      <section className="side-block" aria-labelledby="side-sandcorp-h">
        <h2 id="side-sandcorp-h">{s.sandcorp_title}</h2>
        <p>{s.sandcorp_text}</p>
        <a className="side-link" href={s.sandcorp_url}>
          {s.sandcorp_label}
        </a>
      </section>
      <p className="side-contact">
        <a href={s.contact_url}>{s.contact_label}</a>
      </p>
    </aside>
  );
}

function pageHref(category: string, page: number): string {
  const params = new URLSearchParams();
  if (category) params.set("category", category);
  params.set("page", String(page));
  return `?${params.toString()}`;
}

export function Home({ data }: { data: HomeData }) {
  const { site } = data;
  return (
    <Layout site={site}>
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
      <div className="layout">
        <div className="primary">
          {data.featured.length > 0 && <Hero cards={data.featured} site={site} />}
          <section aria-labelledby="latest-h">
            <h2 id="latest-h">최신 기사</h2>
            <div className="grid">
              {data.articles.length === 0 && <p className="empty">아직 게시된 기사가 없습니다.</p>}
              {data.articles.map((card) => (
                <Card key={card.slug} card={card} site={site} />
              ))}
            </div>
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
          {data.sections.map((section) => (
            <Section key={section.category.slug} section={section} site={site} />
          ))}
        </div>
        <Sidebar data={data} />
      </div>
    </Layout>
  );
}
