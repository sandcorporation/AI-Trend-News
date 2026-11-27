const KEYS = ["kind", "name", "company", "contact", "message"] as const;

type Key = (typeof KEYS)[number];

/** React가 붙기 전에 서버 HTML 문의 폼에 있던 값과 초점. */
export interface ContactDraft {
  values: Partial<Record<Key, string>>;
  focus: {
    name: string;
    value: string;
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
  const active = form.ownerDocument.activeElement;
  const control = Array.from(form.querySelectorAll<Control>("input, textarea"))
    .filter((c) => (KEYS as readonly string[]).includes(c.name))
    .find((c) => c === active);
  if (!control) return { values, focus: null };
  const { selectionStart: start, selectionEnd: end } = control;
  return {
    values,
    focus: {
      name: control.name,
      value: control.value,
      selection: start === null || end === null ? null : [start, end],
    },
  };
}

/** 읽어 둔 칸에 초점과 커서를 되돌린다. 라디오는 값까지 같은 것을 고른다. */
export function restoreFocus(
  form: HTMLFormElement,
  focus: NonNullable<ContactDraft["focus"]>,
): void {
  const control = Array.from(
    form.querySelectorAll<Control>("input, textarea"),
  ).find(
    (c) =>
      c.name === focus.name && (c.type !== "radio" || c.value === focus.value),
  );
  if (!control) return;
  control.focus({ preventScroll: true });
  if (focus.selection) control.setSelectionRange(...focus.selection);
}
