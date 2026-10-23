import type { ReactNode } from "react";
import type { SiteData } from "./types";

/** Header and footer. Sandcorp links are tracked inflow links; contact is the /contact form. */
export function Layout({
  site,
  children,
}: {
  site: SiteData;
  children: ReactNode;
}) {
  return (
    <div className="site">
      <header className="site-header">
        <a className="brand" href="/">
          {site.name}
        </a>
        <a className="header-link" href={site.header_url}>
          {site.sandcorp_label}
        </a>
      </header>
      <main className="main">{children}</main>
      <footer className="site-footer">
        <p>{site.name} - AI와 IT 뉴스를 한국어로 골라 요약하고, 전망을 적어 채점합니다.</p>
        <p>
          <a href={site.contact_url}>{site.contact_label}</a> ·{" "}
          <a href={site.footer_url}>{site.sandcorp_label}</a>
        </p>
      </footer>
    </div>
  );
}
