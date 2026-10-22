import { render, screen } from "@testing-library/react";
import { App } from "./App";
import type { ArticlePageData, CardData, HomeData } from "./types";
import { readPageData } from "./pageData";

test("페이지가 아직 없는 경로는 준비 중 안내를 보여 준다", () => {
  render(<App data={null} />);

  expect(screen.getByRole("heading", { name: "AI Trend News" })).toBeInTheDocument();
  expect(screen.getByText("사이트를 준비하고 있습니다.")).toBeInTheDocument();
});

test("셸에 심긴 #page-data JSON을 읽고, 없거나 깨지면 null을 돌려준다", () => {
  const doc = document.implementation.createHTMLDocument();
  expect(readPageData(doc)).toBeNull();

  doc.body.innerHTML = '<script id="page-data" type="application/json">{"a": 1}</script>';
  expect(readPageData(doc)).toEqual({ a: 1 });

  doc.body.innerHTML = '<script id="page-data" type="application/json">{oops</script>';
  expect(readPageData(doc)).toBeNull();
});

const site = { name: "AI Trend News", sandcorp_url: "https://sandcorp.co.kr", sandcorp_label: "Sandcorp" };
const card: CardData = {
  slug: "rate-freeze",
  title: "금리 동결",
  summary: "기준금리를 동결했다.",
  category: { name: "경제", slug: "economy" },
  tags: ["금리"],
  published_at: "2026-10-22T05:00:00+00:00",
  published_label: "2026년 10월 22일 14:00",
  image_url: "",
  url: "/a/rate-freeze",
};

test("메인은 추천 영역·분야 탭·기사 목록·페이지 이동을 그린다", () => {
  const data: HomeData = {
    kind: "home",
    site,
    categories: [{ name: "경제", slug: "economy" }],
    category: "economy",
    featured: [{ ...card, title: "추천 기사" }],
    articles: [card],
    page: 2,
    num_pages: 3,
  };
  render(<App data={data} />);

  expect(screen.getByRole("heading", { name: "추천" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "추천 기사" })).toHaveAttribute("href", "/a/rate-freeze");
  expect(screen.getByRole("link", { name: "경제" })).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("link", { name: "이전" })).toHaveAttribute("href", "?category=economy&page=1");
  expect(screen.getByRole("link", { name: "다음" })).toHaveAttribute("href", "?category=economy&page=3");
  expect(screen.getAllByRole("link", { name: "Sandcorp" })).toHaveLength(2);
});

test("기사 상세는 요약·태그와 새 창으로 열리는 원문 링크를 그린다", () => {
  const data: ArticlePageData = {
    kind: "article",
    site,
    article: {
      ...card,
      items: [
        { source: "연합뉴스", title: "Original", url: "https://example.com/n/1", published_label: "" },
      ],
    },
  };
  render(<App data={data} />);

  expect(screen.getByRole("heading", { level: 1, name: "금리 동결" })).toBeInTheDocument();
  expect(screen.getByText("#금리")).toBeInTheDocument();
  const link = screen.getByRole("link", { name: "Original" });
  expect(link).toHaveAttribute("href", "https://example.com/n/1");
  expect(link).toHaveAttribute("target", "_blank");
  expect(link).toHaveAttribute("rel", "noopener");
  expect(screen.getByText("연합뉴스")).toBeInTheDocument();
});
