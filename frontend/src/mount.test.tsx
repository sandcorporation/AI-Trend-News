import { act } from "react";
import type { Root } from "react-dom/client";
import { mount } from "./mount";
import type { ContactPageData, SiteData } from "./types";

const site = {
  name: "Foresight",
  today_label: "",
  nav: [],
} as unknown as SiteData;

function contactData(over: Partial<ContactPageData> = {}): ContactPageData {
  return {
    kind: "contact",
    site,
    state: "form",
    form: {
      name: "",
      company: "",
      contact: "",
      message: "",
      kind: "문의",
      errors: {},
    },
    error_summary: [],
    kinds: ["문의", "제안"],
    ...over,
  };
}

// 서버 HTML(contact.html)의 폼에서 값·초점에 관계있는 부분.
const SERVER_FORM = `
  <form method="post" action="/contact" class="contact-form">
    <div class="hp"><input type="text" name="website"></div>
    <fieldset class="kind">
      <input type="radio" name="kind" value="문의" checked>
      <input type="radio" name="kind" value="제안">
    </fieldset>
    <input name="name"><input name="company"><input name="contact">
    <textarea name="message"></textarea>
    <button type="submit">보내기</button>
  </form>`;

let element: HTMLElement;
let root: Root | undefined;

beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  element = document.createElement("div");
  element.innerHTML = SERVER_FORM;
  document.body.append(element);
});

afterEach(() => {
  act(() => root?.unmount());
  element.remove();
  vi.unstubAllGlobals();
});

function control(selector: string): HTMLInputElement {
  const found = element.querySelector<HTMLInputElement>(selector);
  if (!found) throw new Error(`없음: ${selector}`);
  return found;
}

const attach = (data: ContactPageData) =>
  act(() => {
    root = mount(element, data);
  });

test("서버 HTML 폼에 친 값·초점 칸·커서가 마운트 뒤에도 남는다", () => {
  const typed = {
    name: "홍길동",
    company: "샌드",
    contact: "hong@example.com",
    message: "첫 줄\n둘째 줄",
  };
  for (const [name, value] of Object.entries(typed))
    control(`[name="${name}"]`).value = value;
  control('[value="제안"]').checked = true;
  const before = control('[name="message"]');
  before.focus();
  before.setSelectionRange(2, 2);

  attach(contactData());

  for (const [name, value] of Object.entries(typed))
    expect(control(`[name="${name}"]`)).toHaveValue(value);
  expect(control('[value="제안"]')).toBeChecked();
  const after = control('[name="message"]');
  expect(after).not.toBe(before);
  expect(after).toHaveFocus();
  expect([after.selectionStart, after.selectionEnd]).toEqual([2, 2]);
});

test("초점이 있던 라디오는 같은 값의 라디오로 돌아간다", () => {
  control('[value="제안"]').focus();
  attach(contactData());
  expect(control('[value="제안"]')).toHaveFocus();
  expect(control('[value="문의"]')).toBeChecked();
});

test("폼 밖에 초점이 있으면 초점을 옮기지 않는다", () => {
  attach(contactData());
  expect(document.body).toHaveFocus();
});

const ERRORS = {
  error_summary: [{ href: "#contact-name", text: "이름: 필요" }],
};

/** 400 화면의 서버 HTML: 폼 앞에 `autofocus`가 걸린 오류 요약이 있다. */
function focusServerSummary() {
  element.insertAdjacentHTML(
    "afterbegin",
    '<div class="notice err error-summary" role="alert" tabindex="-1"></div>',
  );
  element.querySelector<HTMLElement>(".error-summary")?.focus();
}

test("400 화면에서 마운트 전에 옮긴 칸의 초점과 커서가 남는다", () => {
  focusServerSummary();
  const before = control('[name="message"]');
  before.value = "덧붙인 글";
  before.focus();
  before.setSelectionRange(3, 3);

  attach(contactData(ERRORS));

  const after = control('[name="message"]');
  expect(after).not.toBe(before);
  expect(after).toHaveValue("덧붙인 글");
  expect(after).toHaveFocus();
  expect([after.selectionStart, after.selectionEnd]).toEqual([3, 3]);
});

test("400 화면에서 초점을 옮기지 않았으면 오류 요약에 초점이 있다", () => {
  focusServerSummary();
  const before = element.querySelector(".error-summary");

  attach(contactData(ERRORS));

  const after = element.querySelector(".error-summary");
  expect(after).not.toBe(before);
  expect(after).toHaveFocus();
});

test("400 화면에서 마운트 전에 「보내기」에 둔 초점이 남는다", () => {
  focusServerSummary();
  const before = control('button[type="submit"]');
  before.focus();

  attach(contactData(ERRORS));

  const after = control('button[type="submit"]');
  expect(after).not.toBe(before);
  expect(after).toHaveFocus();
});

test("400 화면에서 마운트 전에 오류 요약 링크에 둔 초점이 남는다", () => {
  const errors = {
    error_summary: [
      ...ERRORS.error_summary,
      { href: "#contact-message", text: "내용: 필요" },
    ],
  };
  focusServerSummary();
  control(".error-summary").innerHTML = errors.error_summary
    .map((e) => `<a href="${e.href}">${e.text}</a>`)
    .join("");
  const before = control('a[href="#contact-message"]');
  before.focus();

  attach(contactData(errors));

  const after = control('a[href="#contact-message"]');
  expect(after).not.toBe(before);
  expect(after).toHaveFocus();
});
