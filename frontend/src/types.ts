/** Shapes of the JSON Django embeds in #page-data (backend/core/public.py). Keep in sync. */
export interface CategoryData {
  name: string;
  slug: string;
}

export interface CardData {
  slug: string;
  title: string;
  summary: string;
  category: CategoryData;
  tags: string[];
  published_at: string;
  published_label: string;
  image_url: string;
  tone: "ai" | "it";
  url: string;
  has_commentary: boolean;
}

export interface PredictionData {
  claim: string;
  horizon: string;
  horizon_label: string;
  basis: string;
  due_label: string;
  /** 미평가면 빈 문자열 */
  verdict: "" | "hit" | "miss" | "undecidable";
  verdict_label: string;
  verdict_note: string;
}

export interface CommentaryData {
  background: string;
  significance: string;
  outlook_bull: string;
  outlook_bear: string;
  opinion: string;
  predictions: PredictionData[];
  watchpoints: string[];
}

export interface SourceItemData {
  source: string;
  title: string;
  url: string;
  published_label: string;
}

export interface DetailData extends CardData {
  items: SourceItemData[];
  commentary: CommentaryData | null;
}

export interface NavItemData {
  label: string;
  url: string;
  current: boolean;
}

export interface SiteData {
  name: string;
  /** 머리띠 날짜(서버 시계, 「2026.10.23 (금)」) */
  today_label: string;
  /** 머리띠 내비: 전체 · 분야들 · 전망 기록표 · 문의 */
  nav: NavItemData[];
  monogram: string;
  sandcorp_label: string;
  header_url: string;
  footer_url: string;
  made_by_label: string;
  made_by_url: string;
  contact_label: string;
  contact_url: string;
  privacy_label: string;
  privacy_url: string;
}

export interface CtaData {
  text: string;
  portfolio_label: string;
  portfolio_url: string;
  contact_label: string;
  contact_url: string;
}

export interface ShareData {
  url: string;
  x_url: string;
}

export interface SectionData {
  category: CategoryData;
  more_url: string;
  articles: CardData[];
}

export interface SidebarData {
  share_label: string;
  share_url: string;
  most_read_title: string;
  sandcorp_title: string;
  sandcorp_text: string;
  sandcorp_label: string;
  sandcorp_url: string;
  contact_label: string;
  contact_url: string;
  prediction_rate: SidebarRateData | null;
}

export interface SidebarRateData {
  title: string;
  percent: number;
  judged: number;
  link_label: string;
  url: string;
}

export interface HomeData {
  kind: "home";
  site: SiteData;
  categories: CategoryData[];
  category: string;
  featured: CardData[];
  articles: CardData[];
  sections: SectionData[];
  most_read: CardData[];
  sidebar: SidebarData;
  page: number;
  num_pages: number;
}

export interface ArticlePageData {
  kind: "article";
  site: SiteData;
  article: DetailData;
  cta: CtaData;
  share: ShareData;
}

export interface DayShareData extends ShareData {
  title: string;
  text: string;
}

export interface DayLinkData {
  label: string;
  url: string;
}

export interface DayTabData {
  label: string;
  url: string;
  current: boolean;
}

export interface DayPageData {
  kind: "day";
  site: SiteData;
  /** KST 날짜 YYYY-MM-DD */
  date: string;
  heading: string;
  count: number;
  /** 분야 slug. 전체면 빈 문자열 */
  category: string;
  tabs: DayTabData[];
  /** 분야 없는 그날 전체 모음 주소 */
  all_url: string;
  articles: CardData[];
  empty_text: string;
  prev: DayLinkData | null;
  next: DayLinkData | null;
  home_url: string;
  share: DayShareData;
}

export interface ContactFormData {
  name: string;
  company: string;
  contact: string;
  message: string;
  kind: string;
  errors: Record<string, string>;
}

export interface ContactPageData {
  kind: "contact";
  site: SiteData;
  state: "form" | "done" | "limited";
  form: ContactFormData;
  kinds: string[];
}

export interface PredictionRowData {
  claim: string;
  horizon_label: string;
  due_label: string;
  verdict: "" | "hit" | "miss" | "undecidable";
  verdict_label: string;
  verdict_note: string;
  article_title: string;
  article_url: string;
}

export interface PredictionsPageData {
  kind: "predictions";
  site: SiteData;
  period: string;
  periods: { key: string; label: string; url: string }[];
  rate: {
    percent: number | null;
    judged: number;
    hits: number;
    undecidable: number;
    pending: number;
  };
  rows: PredictionRowData[];
}

export type PageData =
  | HomeData
  | ArticlePageData
  | DayPageData
  | ContactPageData
  | PredictionsPageData;

export function isPageData(value: unknown): value is PageData {
  if (typeof value !== "object" || value === null) return false;
  const kind = (value as { kind?: unknown }).kind;
  return kind === "home" ||
    kind === "article" ||
    kind === "day" ||
    kind === "contact" ||
    kind === "predictions";
}
