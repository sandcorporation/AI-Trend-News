/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Django renders the HTML shell; Vite only builds the entry script.
// The shell reads dist/.vite/manifest.json, and collectstatic serves dist/ under /static/.
export default defineConfig(({ command }) => ({
  plugins: [react()],
  // The dev server serves from / (the shell points at it directly); builds live under /static/.
  base: command === "build" ? "/static/" : "/",
  server: {
    port: 5173,
    strictPort: true,
    origin: "http://localhost:5173",
  },
  build: {
    manifest: true,
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: { input: "src/main.tsx" },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["src/test-setup.ts"],
  },
}));
