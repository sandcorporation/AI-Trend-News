import { ArticlePage } from "./ArticlePage";
import { Home } from "./Home";
import { isPageData } from "./types";

/** Picks the page from the JSON Django embedded for this route (`readPageData()` in pageData.ts). */
export function App({ data }: { data: unknown }) {
  if (isPageData(data)) {
    return data.kind === "home" ? <Home data={data} /> : <ArticlePage data={data} />;
  }
  return (
    <main className="placeholder">
      <h1>AI Trend News</h1>
      <p>사이트를 준비하고 있습니다.</p>
    </main>
  );
}
