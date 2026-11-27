import { Layout } from "./Layout";
import type { NotFoundPageData } from "./types";

/** 404: 서버 HTML(not_found.html)과 같은 내용. */
export function NotFoundPage({ data }: { data: NotFoundPageData }) {
  return (
    <Layout site={data.site}>
      <section className="not-found" data-slot="not-found">
        <h1>{data.heading}</h1>
        <p>
          <a className="btn" href={data.home_url}>
            {data.home_label}
          </a>
        </p>
        {data.latest.length > 0 && (
          <>
            <h2>{data.latest_title}</h2>
            <ul className="title-list">
              {data.latest.map((a) => (
                <li key={a.url}>
                  <a href={a.url}>{a.title}</a>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>
    </Layout>
  );
}
