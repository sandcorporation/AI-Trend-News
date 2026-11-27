import { ArticlePage } from "./ArticlePage";
import { ContactPage } from "./ContactPage";
import type { ContactDraft } from "./contactDraft";
import { DayPage } from "./DayPage";
import { Home } from "./Home";
import { NotFoundPage } from "./NotFoundPage";
import { PredictionsPage } from "./PredictionsPage";
import { isPageData } from "./types";

/** Picks the page from the JSON Django embedded for this route (`readPageData()` in pageData.ts). */
export function App({
  data,
  contactDraft = null,
}: {
  data: unknown;
  contactDraft?: ContactDraft | null;
}) {
  if (isPageData(data)) {
    if (data.kind === "home") return <Home data={data} />;
    if (data.kind === "not_found") return <NotFoundPage data={data} />;
    if (data.kind === "day") return <DayPage data={data} />;
    if (data.kind === "contact")
      return <ContactPage data={data} draft={contactDraft} />;
    if (data.kind === "predictions") return <PredictionsPage data={data} />;
    return <ArticlePage data={data} />;
  }
  return (
    <main className="placeholder">
      <h1>Foresight</h1>
      <p>사이트를 준비하고 있습니다.</p>
    </main>
  );
}
