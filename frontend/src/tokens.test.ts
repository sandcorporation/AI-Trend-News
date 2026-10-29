import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = __dirname;

function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    return entry.isDirectory() ? walk(path) : [path];
  });
}

/** Every source file's text, keyed by path (tests excluded). Shared tokens and their contrast tests live in sandcorp-design. */
const sources: Record<string, string> = Object.fromEntries(
  walk(SRC)
    .filter((file) => /\.(css|tsx?)$/.test(file) && !/\.test\.tsx?$/.test(file))
    .map((file) => [file, readFileSync(file, "utf8")]),
);

const RAW_COLOR = /#[0-9a-fA-F]{3,8}\b|\b(?:rgb|rgba|hsl|hsla|oklch|oklab|lab|lch)\(/;

describe("design tokens", () => {
  it("raw color values live only in sandcorp-design (none in this app, tokens.css included)", () => {
    const offenders = Object.entries(sources)
      .filter(([file]) => !file.endsWith("types.gen.ts"))
      .filter(([, text]) => RAW_COLOR.test(text))
      .map(([file]) => file);
    expect(Object.keys(sources).length).toBeGreaterThan(8);
    expect(offenders).toEqual([]);
  });
});

describe("font fallbacks", () => {
  const designCss = readFileSync(join(__dirname, "../node_modules/sandcorp-design/dist/sandcorp.css"), "utf8");

  it("come from sandcorp-design; this app keeps no tokens.css of its own", () => {
    expect(existsSync(join(SRC, "tokens.css"))).toBe(false);
    for (const family of ["Pretendard Fallback", "Pretendard Fallback Apple", "Pretendard Fallback Win"]) {
      expect(designCss).toMatch(new RegExp(`font-family:\\s*"?${family}"?[;}]`));
    }
    const stack = /--font-sans:([^;]+);/.exec(designCss)?.[1] ?? "";
    expect(stack.indexOf('"Pretendard Fallback"')).toBeGreaterThan(stack.indexOf("Pretendard,"));
  });
});
