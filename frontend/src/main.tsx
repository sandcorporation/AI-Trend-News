import "./sentryInit";
import { mount } from "./mount";
import { readPageData } from "./pageData";
import "sandcorp-design/css";
import "sandcorp-design/fonts.css";
import "./styles.css";

const root = document.getElementById("root");
if (root) mount(root, readPageData());
