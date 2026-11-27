const KEYS = ["kind", "name", "company", "contact", "message"] as const;

type Key = (typeof KEYS)[number];

/** React가 붙기 전에 서버 HTML 문의 폼에 있던 값과 초점. */
export interface ContactDraft {
  values: Partial<Record<Key, string>>;
  focus: {
    /** 다시 그린 화면에서 같은 요소를 찾는 선택자. */
    selector: string;
    selection: [number, number] | null;
  } | null;
}

type Control = HTMLInputElement | HTMLTextAreaElement;

export function readContactDraft(root: ParentNode): ContactDraft | null {
  const form = root.querySelector<HTMLFormElement>("form.contact-form");
  if (!form) return null;
  const values: ContactDraft["values"] = {};
  for (const key of KEYS) {
    const selector =
      key === "kind" ? '[name="kind"]:checked' : `[name="${key}"]`;
    values[key] = form.querySelector<Control>(selector)?.value ?? "";
  }
  return { values, focus: readFocus(root, form.ownerDocument.activeElement) };
}

/** 폼의 입력칸·버튼과 오류 요약의 링크만 읽는다(요약 자체는 마운트 기본값이다). */
function readFocus(
  root: ParentNode,
  active: Element | null,
): ContactDraft["focus"] {
  const found = Array.from(
    root.querySelectorAll(
      "form.contact-form :is(input, textarea, button), .error-summary a",
    ),
  ).find((e) => e === active);
  if (!found) return null;
  if (found instanceof HTMLAnchorElement)
    return {
      selector: `.error-summary a[href="${found.getAttribute("href")}"]`,
      selection: null,
    };
  if (found instanceof HTMLButtonElement)
    return { selector: "form.contact-form button", selection: null };
  const control = found as Control;
  const radio = control.type === "radio" ? `[value="${control.value}"]` : "";
  const { selectionStart: start, selectionEnd: end } = control;
  return {
    selector: `form.contact-form [name="${control.name}"]${radio}`,
    selection: start === null || end === null ? null : [start, end],
  };
}

/** 읽어 둔 요소에 초점과 커서를 되돌린다. */
export function restoreFocus(
  root: ParentNode,
  focus: NonNullable<ContactDraft["focus"]>,
): void {
  const target = root.querySelector<HTMLElement>(focus.selector);
  if (!target) return;
  target.focus({ preventScroll: true });
  if (focus.selection)
    (target as Control).setSelectionRange(...focus.selection);
}
