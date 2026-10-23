import { Layout } from "./Layout";
import type { PredictionsPageData } from "./types";

/** 전망 기록표: 평가된 예측의 적중률과 목록(서버 HTML과 같은 내용). */
export function PredictionsPage({ data }: { data: PredictionsPageData }) {
  const { rate } = data;
  return (
    <Layout site={data.site}>
      <section className="predictions-page" data-slot="predictions">
        <h1>전망 기록표</h1>
        <p className="lead">
          AI 분석의 예측 문장을 모아 두고, 기한이 지나면 대표가 적중 여부를
          기록합니다. AI가 스스로 채점하지 않습니다.
        </p>
        <nav className="tabs" aria-label="기간">
          {data.periods.map((p) => (
            <a
              key={p.key}
              href={p.url}
              aria-current={p.key === data.period ? "page" : undefined}
            >
              {p.label}
            </a>
          ))}
        </nav>
        <p className="hit-rate" data-slot="hit-rate">
          {rate.percent === null ? (
            "아직 평가된 예측이 없습니다."
          ) : (
            <>
              적중률 <strong>{rate.percent}%</strong>{" "}
              <span>
                평가 {rate.judged}건 중 {rate.hits}건 적중
                {rate.undecidable > 0 &&
                  ` · 판단 불가 ${rate.undecidable}건 제외`}
              </span>
            </>
          )}
        </p>
        {data.rows.length > 0 ? (
          <ul className="prediction-list">
            {data.rows.map((r) => (
              <li key={`${r.article_url}-${r.claim}`}>
                <span
                  className={`verdict verdict-${r.verdict || "pending"}`}
                  data-slot="verdict"
                >
                  {r.verdict_label}
                </span>
                <span className="claim">{r.claim}</span>
                <span className="meta">
                  <a href={r.article_url}>{r.article_title}</a> ·{" "}
                  {r.horizon_label} · 기한 {r.due_label}
                </span>
                {r.verdict_note && <span className="note">{r.verdict_note}</span>}
              </li>
            ))}
          </ul>
        ) : (
          <p className="empty">표시할 예측이 없습니다.</p>
        )}
      </section>
    </Layout>
  );
}
