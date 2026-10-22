import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { readPageData } from "./pageData";
import "./styles.css";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <App data={readPageData()} />
    </StrictMode>,
  );
}
