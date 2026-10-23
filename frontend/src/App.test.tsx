import { act, fireEvent, render, screen } from "@testing-library/react";
import { App } from "./App";
import type { ArticlePageData, CardData, HomeData } from "./types";
import { readPageData } from "./pageData";

test("페이지가 아직 없는 경로는 준비 중 안내를 보여 준다", () => {
  render(<App data={null} />);

  expect(
    screen.getByRole("heading", { name: "AI Trend News" }),
  ).toBeInTheDocument();
  expect(screen.getByText("사이트를 준비하고 있습니다.")).toBeInTheDocument();
});

test("셸에 심긴 #page-data JSON을 읽고, 없거나 깨지면 null을 돌려준다", () => {
  const doc = document.implementation.createHTMLDocument();
  expect(readPageData(doc)).toBeNull();

  doc.body.innerHTML =
    '<script id="page-data" type="application/json">{"a": 1}</script>';
  expect(readPageData(doc)).toEqual({ a: 1 });

  doc.body.innerHTML =
    '<script id="page-data" type="application/json">{oops</script>';
  expect(readPageData(doc)).toBeNull();
});

const site = {
  name: "AI Trend News",
  monogram: "AT",
  sandcorp_label: "Sandcorp",
  header_url: "/go/sandcorp?from=header",
  footer_url: "/go/sandcorp?from=footer",
  contact_label: "문의",
  contact_url: "/contact",
};
const sidebar = {
  most_read_title: "많이 읽은 기사",
  sandcorp_title: "Sandcorp",
  sandcorp_text: "소개",
  sandcorp_label: "Sandcorp 알아보기",
  sandcorp_url: "/go/sandcorp?from=sidebar",
  contact_label: "문의하기",
  contact_url: "/contact",
};
const card: CardData = {
  slug: "rate-freeze",
  title: "금리 동결",
  summary: "기준금리를 동결했다.",
  category: { name: "경제", slug: "economy" },
  tags: ["금리"],
  published_at: "2026-10-22T05:00:00+00:00",
  published_label: "2026년 10월 22일 14:00",
  image_url: "",
  tone: "ai",
  url: "/a/rate-freeze",
  has_commentary: false,
};

test("메인은 추천 영역·분야 탭·기사 목록·페이지 이동을 그린다", () => {
  const data: HomeData = {
    kind: "home",
    site,
    categories: [{ name: "경제", slug: "economy" }],
    category: "economy",
    featured: [{ ...card, title: "추천 기사" }],
    articles: [card],
    sections: [],
    most_read: [],
    sidebar,
    page: 2,
    num_pages: 3,
  };
  render(<App data={data} />);

  expect(screen.getByRole("link", { name: "추천 기사" })).toHaveAttribute(
    "href",
    "/a/rate-freeze",
  );
  expect(screen.getByRole("link", { name: "경제" })).toHaveAttribute(
    "aria-current",
    "page",
  );
  expect(screen.getByRole("link", { name: "이전" })).toHaveAttribute(
    "href",
    "?category=economy&page=1",
  );
  expect(screen.getByRole("link", { name: "다음" })).toHaveAttribute(
    "href",
    "?category=economy&page=3",
  );
  const links = screen.getAllByRole("link", { name: "Sandcorp" });
  expect(links.map((l) => l.getAttribute("href"))).toEqual([
    "/go/sandcorp?from=header",
    "/go/sandcorp?from=footer",
  ]);
});

function q(root: ParentNode, selector: string): Element {
  const found = root.querySelector(selector);
  if (!found) throw new Error(`없음: ${selector}`);
  return found;
}

function homeData(): HomeData {
  const c = (n: number, extra: Partial<CardData> = {}): CardData => ({
    ...card,
    slug: `a-${n}`,
    title: `기사 ${n}`,
    url: `/a/a-${n}`,
    published_label: `${n}시간 전`,
    ...extra,
  });
  return {
    kind: "home",
    site,
    categories: [
      { name: "AI", slug: "ai" },
      { name: "IT", slug: "it" },
    ],
    category: "",
    featured: [
      c(1, { image_url: "https://img.example.com/1.png" }),
      c(2),
      c(3),
    ],
    articles: [c(4), c(5)],
    sections: [
      {
        category: { name: "AI", slug: "ai" },
        more_url: "/?category=ai",
        articles: [c(6), c(7), c(8), c(9)],
      },
      {
        category: { name: "IT", slug: "it" },
        more_url: "/?category=it",
        articles: [c(10, { tone: "it" })],
      },
    ],
    most_read: [c(11), c(12)],
    sidebar,
    page: 1,
    num_pages: 1,
  };
}

test("메인 첫 쪽은 히어로·최신 그리드·분야 섹션·사이드바를 그린다", () => {
  const { container } = render(<App data={homeData()} />);

  const hero = q(container, '[data-slot="hero"]');
  expect(hero.querySelectorAll("article")).toHaveLength(3);
  const heroImg = q(hero, "img");
  expect(heroImg).toHaveAttribute("referrerpolicy", "no-referrer");
  expect(heroImg).toHaveAttribute("loading", "eager");

  expect(container.querySelectorAll(".grid .card")).toHaveLength(2);
  expect(container.querySelectorAll(".grid time")[0]).toHaveTextContent(
    "4시간 전",
  );

  const more = screen.getAllByRole("link", { name: "더보기 →" });
  expect(more.map((l) => l.getAttribute("href"))).toEqual([
    "/?category=ai",
    "/?category=it",
  ]);
  const aiSection = q(container, ".cat-section");
  expect(aiSection.querySelectorAll(".card-feature")).toHaveLength(1);
  expect(aiSection.querySelectorAll(".title-list li")).toHaveLength(3);

  expect(
    screen.getByRole("heading", { name: "많이 읽은 기사" }),
  ).toBeInTheDocument();
  expect(container.querySelectorAll(".ranked li")).toHaveLength(2);
  expect(
    screen.getByRole("link", { name: "Sandcorp 알아보기" }),
  ).toHaveAttribute("href", "/go/sandcorp?from=sidebar");
  expect(screen.getByRole("link", { name: "문의하기" })).toHaveAttribute(
    "href",
    "/contact",
  );
});

test("히어로·섹션·많이 읽은 기사가 비면 그 블록을 그리지 않는다", () => {
  const data = { ...homeData(), featured: [], sections: [], most_read: [] };
  const { container } = render(<App data={data} />);

  expect(container.querySelector('[data-slot="hero"]')).toBeNull();
  expect(container.querySelector(".cat-section")).toBeNull();
  expect(screen.queryByText("많이 읽은 기사")).toBeNull();
  expect(
    screen.getByRole("link", { name: "Sandcorp 알아보기" }),
  ).toBeInTheDocument();
});

test("이미지가 깨지면 이미지를 빼고 분야 색 모노그램 자리를 남긴다", () => {
  const { container } = render(<App data={homeData()} />);
  const img = q(container, '[data-slot="hero"] img');

  fireEvent.error(img);

  expect(container.querySelector('[data-slot="hero"] img')).toBeNull();
  expect(
    container.querySelector('[data-slot="hero"] .thumb.tone-ai'),
  ).toHaveTextContent("AT");
});

test("기사 상세는 요약·태그와 새 창으로 열리는 원문 링크를 그린다", () => {
  const data: ArticlePageData = {
    kind: "article",
    site,
    cta: {
      text: "반복 업무를 자동화하고 싶으신가요?",
      portfolio_label: "Sandcorp 포트폴리오 보기",
      portfolio_url: "/go/sandcorp?from=article_cta&article=rate-freeze",
      contact_label: "문의하기",
      contact_url: "/contact",
    },
    share: {
      url: "https://news.sandcorp.co.kr/a/rate-freeze",
      x_url: "https://twitter.com/intent/tweet?text=x&url=y",
    },
    article: {
      ...card,
      commentary: null,
      items: [
        {
          source: "연합뉴스",
          title: "Original",
          url: "https://example.com/n/1",
          published_label: "",
        },
      ],
    },
  };
  render(<App data={data} />);

  expect(
    screen.getByRole("heading", { level: 1, name: "금리 동결" }),
  ).toBeInTheDocument();
  expect(screen.getByText("#금리")).toBeInTheDocument();
  const link = screen.getByRole("link", { name: "Original" });
  expect(link).toHaveAttribute("href", "https://example.com/n/1");
  expect(link).toHaveAttribute("target", "_blank");
  expect(link).toHaveAttribute("rel", "noopener");
  expect(screen.getByText("연합뉴스")).toBeInTheDocument();
});

function articleData(): ArticlePageData {
  return {
    kind: "article",
    site,
    cta: {
      text: "CTA",
      portfolio_label: "Sandcorp 포트폴리오 보기",
      portfolio_url: "/go/sandcorp?from=article_cta&article=rate-freeze",
      contact_label: "문의하기",
      contact_url: "/contact",
    },
    share: {
      url: "https://news.sandcorp.co.kr/a/rate-freeze",
      x_url: "https://twitter.com/intent/tweet?x=1",
    },
    article: { ...card, items: [], commentary: null },
  };
}

test("기사 상세 하단에 CTA 링크와 X 공유 링크가 있다", () => {
  render(<App data={articleData()} />);

  expect(
    screen.getByRole("link", { name: "Sandcorp 포트폴리오 보기" }),
  ).toHaveAttribute(
    "href",
    "/go/sandcorp?from=article_cta&article=rate-freeze",
  );
  expect(screen.getByRole("link", { name: "문의하기" })).toHaveAttribute(
    "href",
    "/contact",
  );
  const x = screen.getByRole("link", { name: "X에 공유" });
  expect(x).toHaveAttribute("href", "https://twitter.com/intent/tweet?x=1");
  expect(x).toHaveAttribute("target", "_blank");
});

test("링크 복사는 주소를 클립보드에 넣고 복사됨을 보여 준다", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
  });
  render(<App data={articleData()} />);

  fireEvent.click(screen.getByRole("button", { name: "링크 복사" }));

  expect(writeText).toHaveBeenCalledWith(
    "https://news.sandcorp.co.kr/a/rate-freeze",
  );
  expect(
    await screen.findByRole("button", { name: "복사됨" }),
  ).toBeInTheDocument();
});

test("클립보드가 막히면 주소를 선택된 입력으로 보여 준다", async () => {
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText: vi.fn().mockRejectedValue(new Error("denied")) },
    configurable: true,
  });
  render(<App data={articleData()} />);

  fireEvent.click(screen.getByRole("button", { name: "링크 복사" }));

  expect(await screen.findByLabelText("기사 주소")).toHaveValue(
    "https://news.sandcorp.co.kr/a/rate-freeze",
  );
});

test("문의 폼은 필드·종류·honeypot을 그리고, 접수 뒤에는 확인을 보여 준다", () => {
  const form = {
    name: "",
    company: "",
    contact: "",
    message: "",
    kind: "문의",
    errors: {},
  };
  const { container, rerender } = render(
    <App
      data={{
        kind: "contact",
        site,
        state: "form",
        form,
        kinds: ["문의", "제안"],
      }}
    />,
  );
  expect(screen.getByRole("radio", { name: "문의" })).toBeChecked();
  expect(screen.getByRole("radio", { name: "제안" })).not.toBeChecked();
  expect(container.querySelector('input[name="website"]')).not.toBeNull();
  expect(screen.getByRole("link", { name: "문의" })).toHaveAttribute(
    "href",
    "/contact",
  );

  rerender(
    <App
      data={{
        kind: "contact",
        site,
        state: "done",
        form,
        kinds: ["문의", "제안"],
      }}
    />,
  );
  expect(screen.getByText(/접수했습니다/)).toBeInTheDocument();
});

describe("히어로 캐러셀", () => {
  const active = (c: HTMLElement) =>
    q(c, '[data-slot="hero"] .hero-slide.is-active');
  const label = (c: HTMLElement) => active(c).getAttribute("aria-label");
  const tick = (ms: number) => act(() => void vi.advanceTimersByTime(ms));

  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  test("접근성 속성: 캐러셀 영역·장 라벨·숨긴 장 aria-hidden+inert", () => {
    const { container } = render(<App data={homeData()} />);
    const hero = q(container, '[data-slot="hero"]');
    expect(hero).toHaveAttribute("aria-roledescription", "carousel");
    const slides = hero.querySelectorAll(".hero-slide");
    expect([...slides].map((e) => e.getAttribute("aria-label"))).toEqual([
      "1 / 3",
      "2 / 3",
      "3 / 3",
    ]);
    expect(slides[0]).not.toHaveAttribute("aria-hidden");
    expect(slides[1]).toHaveAttribute("aria-hidden", "true");
    expect(slides[1]).toHaveAttribute("inert");
  });

  test("5초마다 다음 장으로 넘어가고 끝에서 처음으로 돈다", () => {
    const { container } = render(<App data={homeData()} />);
    expect(label(container)).toBe("1 / 3");
    tick(4999);
    expect(label(container)).toBe("1 / 3");
    tick(1);
    expect(label(container)).toBe("2 / 3");
    tick(10000);
    expect(label(container)).toBe("1 / 3");
  });

  test("마우스 호버 중에는 멈추고 벗어나면 다시 간다", () => {
    const { container } = render(<App data={homeData()} />);
    const hero = q(container, '[data-slot="hero"]');
    fireEvent.mouseEnter(hero);
    tick(20000);
    expect(label(container)).toBe("1 / 3");
    fireEvent.mouseLeave(hero);
    tick(5000);
    expect(label(container)).toBe("2 / 3");
  });

  test("일시정지 버튼은 자동 넘김을 끄고 다시 켠다", () => {
    const { container } = render(<App data={homeData()} />);
    fireEvent.click(screen.getByRole("button", { name: "자동 넘김 일시정지" }));
    tick(20000);
    expect(label(container)).toBe("1 / 3");
    fireEvent.click(screen.getByRole("button", { name: "자동 넘김 재생" }));
    tick(5000);
    expect(label(container)).toBe("2 / 3");
  });

  test("탭이 숨겨지면 멈춘다", () => {
    const { container } = render(<App data={homeData()} />);
    Object.defineProperty(document, "hidden", {
      configurable: true,
      value: true,
    });
    act(() => void document.dispatchEvent(new Event("visibilitychange")));
    tick(20000);
    expect(label(container)).toBe("1 / 3");
    Object.defineProperty(document, "hidden", {
      configurable: true,
      value: false,
    });
    act(() => void document.dispatchEvent(new Event("visibilitychange")));
    tick(5000);
    expect(label(container)).toBe("2 / 3");
  });

  test("reduced-motion이면 자동 넘김이 없고 수동 이동은 된다", () => {
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: query.includes("reduce"),
      media: query,
    }));
    const { container } = render(<App data={homeData()} />);
    tick(30000);
    expect(label(container)).toBe("1 / 3");
    expect(screen.queryByRole("button", { name: /자동 넘김/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "다음 장" }));
    expect(label(container)).toBe("2 / 3");
  });

  test("점·화살표·키보드·스와이프로 이동하고, 이동하면 5초를 다시 센다", () => {
    const { container } = render(<App data={homeData()} />);
    const hero = q(container, '[data-slot="hero"]');
    tick(4000);
    fireEvent.click(screen.getByRole("button", { name: "3번째 장으로" }));
    expect(label(container)).toBe("3 / 3");
    expect(
      screen.getByRole("button", { name: "3번째 장으로" }),
    ).toHaveAttribute("aria-current", "true");
    tick(4000);
    expect(label(container)).toBe("3 / 3");
    fireEvent.click(screen.getByRole("button", { name: "다음 장" }));
    expect(label(container)).toBe("1 / 3");
    fireEvent.click(screen.getByRole("button", { name: "이전 장" }));
    expect(label(container)).toBe("3 / 3");
    fireEvent.keyDown(hero, { key: "ArrowRight" });
    expect(label(container)).toBe("1 / 3");
    fireEvent.keyDown(hero, { key: "ArrowLeft" });
    expect(label(container)).toBe("3 / 3");
    fireEvent.touchStart(hero, { touches: [{ clientX: 200 }] });
    fireEvent.touchEnd(hero, { changedTouches: [{ clientX: 100 }] });
    expect(label(container)).toBe("1 / 3");
  });

  test("추천이 1건이면 점·화살표 없이 정적이다", () => {
    const data = { ...homeData(), featured: homeData().featured.slice(0, 1) };
    const { container } = render(<App data={data} />);
    expect(container.querySelector(".hero-dot")).toBeNull();
    expect(screen.queryByRole("button", { name: "다음 장" })).toBeNull();
    tick(20000);
    expect(label(container)).toBe("1 / 1");
  });
});

test("분석이 있으면 두 관점·의견·예측 목록을 그리고, 없으면 블록이 없다", () => {
  const data = articleData();
  const { container, rerender } = render(<App data={data} />);
  expect(container.querySelector('[data-slot="commentary"]')).toBeNull();

  data.article.commentary = {
    background: "배경 문장",
    significance: "의미 문장",
    outlook_bull: "낙관 문장",
    outlook_bear: "회의 문장",
    opinion: "의견 문장",
    predictions: [
      {
        claim: "금리가 내린다",
        horizon: "3m",
        horizon_label: "3개월 안",
        basis: "물가 둔화",
      },
    ],
  };
  rerender(<App data={{ ...data }} />);
  const block = q(container, '[data-slot="commentary"]');
  expect(block).toHaveTextContent("AI 분석");
  expect(block).toHaveTextContent("수집된 보도만을 근거로 쓴 분석");
  expect(q(block, ".outlook-bull")).toHaveTextContent("낙관 문장");
  expect(q(block, ".outlook-bear")).toHaveTextContent("회의 문장");
  expect(q(block, ".opinion")).toHaveTextContent("의견 문장");
  expect(q(block, ".predictions")).toHaveTextContent("3개월 안");
  expect(q(block, ".predictions")).toHaveTextContent("근거: 물가 둔화");
  // 요약 아래, 출처 위
  const html = container.innerHTML;
  expect(html.indexOf('class="summary"')).toBeLessThan(html.indexOf("commentary"));
  expect(html.indexOf("commentary")).toBeLessThan(html.indexOf('id="sources-h"'));

  data.article.commentary = { ...data.article.commentary, predictions: [] };
  rerender(<App data={{ ...data }} />);
  expect(container.querySelector(".predictions")).toBeNull();
});

test("분석 배지는 메인 카드에만 있고 캐러셀 장에는 없다", () => {
  const data = homeData();
  data.featured = data.featured.map((f) => ({ ...f, has_commentary: true }));
  data.articles = data.articles.map((f, i) => ({ ...f, has_commentary: i === 0 }));
  const { container } = render(<App data={data} />);
  expect(container.querySelector('[data-slot="hero"] .badge-analysis')).toBeNull();
  expect(container.querySelectorAll(".card .badge-analysis")).toHaveLength(1);
});
