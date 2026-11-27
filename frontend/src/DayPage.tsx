import { useRef, useState } from "react";
import { Card } from "./Home";
import { Layout } from "./Layout";
import type { DayPageData } from "./types";

/** 주소 입력칸 + 「링크 복사」. 서버 HTML과 같은 조각이며, 서버에서는 JS가 필요한 버튼만 숨긴다. */
function ShareUrl({ url }: { url: string }) {
  const input = useRef<HTMLInputElement>(null);
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
    } catch {
      // 클립보드가 막히면 손으로 복사할 수 있게 주소를 고른다.
      setCopied(false);
      input.current?.focus();
      input.current?.select();
    }
  };
  return (
    <>
      <input
        ref={input}
        className="share-url"
        readOnly
        value={url}
        aria-label="공유 주소"
        onFocus={(e) => e.currentTarget.select()}
      />
      <button className="btn" type="button" onClick={copy}>
        {copied ? "복사됨" : "링크 복사"}
      </button>
    </>
  );
}

/** 「공유…」: 브라우저가 Web Share를 지원할 때만 보인다(모바일에서 카카오톡을 고를 수 있다). */
function NativeShareButton({
  title,
  text,
  url,
}: {
  title: string;
  text: string;
  url: string;
}) {
  const [supported] = useState(
    () => typeof navigator !== "undefined" && typeof navigator.share === "function",
  );
  if (!supported) return null;
  const share = async () => {
    try {
      await navigator.share({ title, text, url });
    } catch {
      // 사용자가 닫은 경우 등: 할 일이 없다.
    }
  };
  return (
    <button className="btn" type="button" onClick={share}>
      공유…
    </button>
  );
}

/** 날짜별 뉴스: 하루치 기사 모음과 공유 조각(서버 HTML과 같은 내용). */
export function DayPage({ data }: { data: DayPageData }) {
  return (
    <Layout site={data.site}>
      <section className="day-page" data-slot="day">
        <p className="label">
          <a href={data.home_url}>← 홈</a>
        </p>
        <h1>{data.heading}</h1>
        <p className="lead">
          {data.count > 0 ? `게시된 기사 ${data.count}건` : data.empty_text}
        </p>
        <nav className="day-tabs" aria-label="분야" data-slot="day-tabs">
          {data.tabs.map((tab) => (
            <a
              key={tab.url}
              href={tab.url}
              aria-current={tab.current ? "page" : undefined}
            >
              {tab.label}
            </a>
          ))}
        </nav>
        <div className="share" data-slot="share">
          <ShareUrl url={data.share.url} />
          <a
            className="btn"
            href={data.share.x_url}
            target="_blank"
            rel="noopener"
          >
            X에 공유
          </a>
          <NativeShareButton
            title={data.share.title}
            text={data.share.text}
            url={data.share.url}
          />
        </div>
        {data.count === 0 && data.category && (
          <p data-slot="day-all">
            <a href={data.all_url}>이 날 전체 뉴스 보기 →</a>
          </p>
        )}
        {data.articles.length > 0 && (
          <div className="grid">
            {data.articles.map((card) => (
              <Card key={card.slug} card={card} site={data.site} />
            ))}
          </div>
        )}
        {(data.prev || data.next) && (
          <nav className="day-nav" aria-label="다른 날" data-slot="day-nav">
            {data.prev ? (
              <a rel="prev" href={data.prev.url}>
                ← {data.prev.label}
              </a>
            ) : (
              <span />
            )}
            {data.next && (
              <a rel="next" href={data.next.url}>
                {data.next.label} →
              </a>
            )}
          </nav>
        )}
      </section>
    </Layout>
  );
}
