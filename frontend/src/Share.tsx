import { useRef, useState } from "react";
import type { ShareData } from "./types";

const COPY_MESSAGE = {
  idle: "",
  copied: "주소를 복사했습니다",
  failed: "복사하지 못했습니다. 주소를 직접 복사하세요.",
};

/** 주소 입력칸 + 「링크 복사」 + 결과 알림. */
function ShareUrl({ url }: { url: string }) {
  const input = useRef<HTMLInputElement>(null);
  const [state, setState] = useState<keyof typeof COPY_MESSAGE>("idle");
  const copy = async (button: HTMLButtonElement) => {
    // 같은 결과가 다시 나와도 알리도록 비우고 시작한다.
    setState("idle");
    try {
      await navigator.clipboard.writeText(url);
      setState("copied");
    } catch {
      setState("failed");
      // 그사이 초점을 옮겼으면 빼앗지 않는다. 아직 버튼에 있으면 손으로 복사하게 주소를 고른다.
      if (document.activeElement === button) {
        input.current?.focus();
        input.current?.select();
      }
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
      <button className="btn" type="button" onClick={(e) => copy(e.currentTarget)}>
        {state === "copied" ? "복사됨" : "링크 복사"}
      </button>
      <span
        className={state === "failed" ? "share-status" : "sr-only"}
        role="status"
        aria-live="polite"
      >
        {COPY_MESSAGE[state]}
      </span>
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

/** 기사·날짜 페이지의 공유 조각. 서버 HTML `_share.html`과 같은 항목이고 「공유…」만 React 전용이다. */
export function ShareFragment({
  share,
  title,
  text,
}: {
  share: ShareData;
  title: string;
  text: string;
}) {
  return (
    <div className="share" data-slot="share">
      <ShareUrl url={share.url} />
      <a className="btn" href={share.x_url} target="_blank" rel="noopener">
        X에 공유
      </a>
      <NativeShareButton title={title} text={text} url={share.url} />
    </div>
  );
}
