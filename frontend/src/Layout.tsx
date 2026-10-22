import type { ReactNode } from "react";
import type { SiteData } from "./types";

/** Header and footer. #12 swaps `site.sandcorp_url` for the tracked inflow link; #13 adds contact. */
export function Layout({ site, children }: { site: SiteData; children: ReactNode }) {
  return (
    <div className="site">
      <header className="site-header">
        <a className="brand" href="/">
          {site.name}
        </a>
        <a className="header-link" href={site.sandcorp_url}>
          {site.sandcorp_label}
        </a>
      </header>
      <main className="main">{children}</main>
      <footer className="site-footer">
        <p>{site.name} - 여러 분야의 뉴스를 한국어로 골라 요약합니다.</p>
        <p>
          <a href={site.sandcorp_url}>{site.sandcorp_label}</a>
        </p>
      </footer>
    </div>
  );
}
