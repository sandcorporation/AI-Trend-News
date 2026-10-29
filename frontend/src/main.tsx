import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { readPageData } from "./pageData";
import "sandcorp-design/css";
import "sandcorp-design/fonts.css";
import "./tokens.css";
import "./styles.css";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <App data={readPageData()} />
    </StrictMode>,
  );
}
