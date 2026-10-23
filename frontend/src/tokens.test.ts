import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = __dirname;

function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    return entry.isDirectory() ? walk(path) : [path];
  });
}

/** Every source file's text, keyed by path (tests excluded). Ported from portfolio (#73) for #72. */
const sources: Record<string, string> = Object.fromEntries(
  walk(SRC)
    .filter((file) => /\.(css|tsx?)$/.test(file) && !/\.test\.tsx?$/.test(file))
    .map((file) => [file, readFileSync(file, "utf8")]),
);

const RAW_COLOR = /#[0-9a-fA-F]{3,8}\b|\b(?:rgb|rgba|hsl|hsla|oklch|oklab|lab|lch)\(/;

describe("design tokens", () => {
  it("raw color values live only in tokens.css", () => {
    const offenders = Object.entries(sources)
      .filter(([file]) => !file.endsWith("tokens.css") && !file.endsWith("types.gen.ts"))
      .filter(([, text]) => RAW_COLOR.test(text))
      .map(([file]) => file);
    expect(Object.keys(sources).length).toBeGreaterThan(8);
    expect(offenders).toEqual([]);
  });
});

// WCAG relative luminance and contrast ratio.
function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

/** Token values of one scheme: the light block is the first `:root`, dark the one in the media query. */
function tokens(scheme: "light" | "dark"): Record<string, string> {
  const css = sources[join(SRC, "tokens.css")] ?? "";
  const [light = "", dark = ""] = css.split("@media screen and (prefers-color-scheme: dark)");
  const block = scheme === "light" ? light : dark;
  const values: Record<string, string> = {};
  for (const [, name, value] of block.matchAll(/--([\w-]+):\s*(#[0-9a-fA-F]{6})\s*;/g)) {
    if (name && value) values[name] = value;
  }
  return values;
}

describe.each(["light", "dark"] as const)("contrast (%s)", (scheme) => {
  const light = tokens("light");
  const t = { ...light, ...tokens(scheme) };
  const get = (name: string) => t[name] ?? "";
  const surfaces = ["color-canvas", "color-paper", "color-tint"];

  it.each([
    "color-text",
    "color-ink",
    "color-muted",
    "color-accent",
    "color-cat-ai",
    "color-cat-it",
  ])("%s is >= 4.5:1 on every surface", (fg) => {
    for (const bg of surfaces) {
      expect(contrast(get(fg), get(bg)), `${fg} on ${bg}`).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("accent is >= 4.5:1 on the accent tint", () => {
    for (const bg of ["color-accent-tint"]) {
      expect(contrast(get("color-accent"), get(bg)), bg).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("primary button (canvas on ink) is >= 4.5:1", () => {
    expect(contrast(get("color-canvas"), get("color-ink"))).toBeGreaterThanOrEqual(4.5);
  });

  it("text on the accent fill is >= 4.5:1", () => {
    expect(contrast(get("color-on-accent"), get("color-accent"))).toBeGreaterThanOrEqual(4.5);
  });

  it("text stays readable on the accent tint", () => {
    expect(contrast(get("color-text"), get("color-accent-tint"))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(get("color-muted"), get("color-accent-tint"))).toBeGreaterThanOrEqual(4.5);
  });
});
