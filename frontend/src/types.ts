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
}

export interface CommentaryData {
  background: string;
  significance: string;
  outlook_bull: string;
  outlook_bear: string;
  opinion: string;
  predictions: PredictionData[];
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

export interface SiteData {
  name: string;
  monogram: string;
  sandcorp_label: string;
  header_url: string;
  footer_url: string;
  contact_label: string;
  contact_url: string;
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
  most_read_title: string;
  sandcorp_title: string;
  sandcorp_text: string;
  sandcorp_label: string;
  sandcorp_url: string;
  contact_label: string;
  contact_url: string;
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

export type PageData = HomeData | ArticlePageData | ContactPageData;

export function isPageData(value: unknown): value is PageData {
  if (typeof value !== "object" || value === null) return false;
  const kind = (value as { kind?: unknown }).kind;
  return kind === "home" || kind === "article" || kind === "contact";
}
