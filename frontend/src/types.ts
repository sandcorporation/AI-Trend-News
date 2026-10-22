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
  url: string;
}

export interface SourceItemData {
  source: string;
  title: string;
  url: string;
  published_label: string;
}

export interface DetailData extends CardData {
  items: SourceItemData[];
}

export interface SiteData {
  name: string;
  sandcorp_label: string;
  header_url: string;
  footer_url: string;
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

export interface HomeData {
  kind: "home";
  site: SiteData;
  categories: CategoryData[];
  category: string;
  featured: CardData[];
  articles: CardData[];
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

export type PageData = HomeData | ArticlePageData;

export function isPageData(value: unknown): value is PageData {
  if (typeof value !== "object" || value === null) return false;
  const kind = (value as { kind?: unknown }).kind;
  return kind === "home" || kind === "article";
}
