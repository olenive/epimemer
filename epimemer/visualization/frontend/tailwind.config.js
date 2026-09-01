/** @type {import('tailwindcss').Config} */
export default {
  // Driven by a `dark` class on <html>, written only by `theme.ts`, so the
  // choice can be persisted and toggled rather than following the OS blindly.
  darkMode: "class",
  // Test files are excluded: `layout.test.ts` names raw grey classes as
  // fixtures for the guard that forbids them, and Tailwind would compile
  // those into the bundle as real utilities.
  content: ["./index.html", "./src/**/*.{ts,tsx}", "!./src/**/*.test.ts"],
  theme: {
    extend: {
      // Semantic names backed by the custom properties in `src/tokens.css`, so
      // the dark theme is a second set of values rather than a second set of
      // classes: `bg-surface-chrome` replaces `bg-gray-300 dark:bg-gray-900`.
      //
      // `<alpha-value>` is what keeps the opacity modifiers working, and it is
      // why the tokens hold channels rather than hex.
      colors: {
        surface: {
          page: "rgb(var(--surface-page) / <alpha-value>)",
          chrome: "rgb(var(--surface-chrome) / <alpha-value>)",
          raised: "rgb(var(--surface-raised) / <alpha-value>)",
          "raised-hover": "rgb(var(--surface-raised-hover) / <alpha-value>)",
        },
        // Dividers and outlines. Named `line` rather than `border` so the class
        // reads `border-line` instead of `border-border`.
        line: "rgb(var(--border) / <alpha-value>)",
        content: {
          strong: "rgb(var(--text-strong) / <alpha-value>)",
          primary: "rgb(var(--text-primary) / <alpha-value>)",
          secondary: "rgb(var(--text-secondary) / <alpha-value>)",
          muted: "rgb(var(--text-muted) / <alpha-value>)",
        },
      },
    },
  },
  plugins: [],
};
