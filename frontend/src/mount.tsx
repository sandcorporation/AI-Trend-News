import { StrictMode } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";
import { App } from "./App";
import { readContactDraft } from "./contactDraft";

/** 서버 HTML이 든 `element`를 React 화면으로 바꾼다. */
export function mount(element: HTMLElement, data: unknown): Root {
  const draft = readContactDraft(element);
  const root = createRoot(element);
  const render = () =>
    root.render(
      <StrictMode>
        <App data={data} contactDraft={draft} />
      </StrictMode>,
    );
  // 문의 폼은 읽기와 DOM 교체 사이에 친 글자가 사라지지 않게 곧바로 그린다.
  if (draft) flushSync(render);
  else render();
  return root;
}
