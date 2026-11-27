import { Card } from "./Home";
import { Layout } from "./Layout";
import { ShareFragment } from "./Share";
import type { DayPageData } from "./types";

/** 날짜별 뉴스: 하루치 기사 모음과 공유 조각(서버 HTML과 같은 내용). */
export function DayPage({ data }: { data: DayPageData }) {
  return (
    <Layout site={data.site}>
      <section className="day-page" data-slot="day">
        <p className="label">
          <a href={data.home_url}>← 홈</a>
        </p>
        <h1>{data.heading}</h1>
        <p className="lead">
          {data.count > 0 ? `게시된 기사 ${data.count}건` : data.empty_text}
        </p>
        <nav className="day-tabs" aria-label="분야" data-slot="day-tabs">
          {data.tabs.map((tab) => (
            <a
              key={tab.url}
              href={tab.url}
              aria-current={tab.current ? "page" : undefined}
            >
              {tab.label}
            </a>
          ))}
        </nav>
        <ShareFragment
          share={data.share}
          title={data.share.title}
          text={data.share.text}
        />
        {data.count === 0 && data.category && (
          <p data-slot="day-all">
            <a href={data.all_url}>이 날 전체 뉴스 보기 →</a>
          </p>
        )}
        {data.articles.length > 0 && (
          <div className="grid">
            {data.articles.map((card) => (
              <Card key={card.slug} card={card} site={data.site} />
            ))}
          </div>
        )}
        {(data.prev || data.next) && (
          <nav className="day-nav" aria-label="다른 날" data-slot="day-nav">
            {data.prev ? (
              <a rel="prev" href={data.prev.url}>
                ← {data.prev.label}
              </a>
            ) : (
              <span />
            )}
            {data.next && (
              <a rel="next" href={data.next.url}>
                {data.next.label} →
              </a>
            )}
          </nav>
        )}
      </section>
    </Layout>
  );
}
