import type { ReactNode } from "react";
import type { SiteData } from "./types";

/** Masthead, site nav and footer. Sandcorp links are tracked inflow links; contact is the /contact form. */
export function Layout({
  site,
  children,
}: {
  site: SiteData;
  children: ReactNode;
}) {
  return (
    <div className="site">
      <header className="masthead">
        <div className="masthead-row">
          <p className="masthead-brand">
            <a className="brand" href="/">
              {site.name}
            </a>{" "}
            <span className="label">포사이트 · AI·IT 뉴스와 전망</span>
          </p>
          <p className="masthead-meta label">
            <span>{site.today_label}</span> · <span>하루 3회 07·13·19 KST</span>{" "}
            ·{" "}
            <a className="header-link" href={site.header_url}>
              {site.sandcorp_label}
            </a>
          </p>
        </div>
        <nav className="site-nav" aria-label="사이트">
          {site.nav.map((n) => (
            <a
              key={n.url}
              href={n.url}
              aria-current={n.current ? "page" : undefined}
            >
              {n.label}
            </a>
          ))}
        </nav>
      </header>
      <main className="main">{children}</main>
      <footer className="site-footer">
        <p className="footer-lead">
          {site.name} — AI와 IT 뉴스를 한국어로 골라 요약하고, 전망을 적어
          채점합니다.
        </p>
        <p className="footer-links label">
          <a href={site.footer_url}>{site.sandcorp_label}</a> ·{" "}
          <a href={site.contact_url}>{site.contact_label}</a> ·{" "}
          <a href="/predictions">전망 기록표</a> ·{" "}
          <a href="/sitemap.xml">sitemap</a>
        </p>
        <p className="footer-copy label">© Sandcorp · {site.name}</p>
      </footer>
    </div>
  );
}
