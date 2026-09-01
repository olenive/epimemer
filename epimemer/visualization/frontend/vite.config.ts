import { defineConfig } from "vite";

export default defineConfig({
  build: {
    outDir: "../static",
    emptyOutDir: true,
  },
  test: {
    // Process CSS in tests, so `tokens.css` can be read back and checked
    // against the defaults `theme.ts` falls back to. Without it a `?inline`
    // import resolves to an empty string and the guard passes on nothing.
    css: true,
  },
});
