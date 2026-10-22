import { render, screen } from "@testing-library/react";
import { App } from "./App";
import { readPageData } from "./pageData";

test("페이지가 아직 없는 경로는 준비 중 안내를 보여 준다", () => {
  render(<App />);

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
