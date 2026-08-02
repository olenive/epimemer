/** @type {import('tailwindcss').Config} */
export default {
  // Driven by a `dark` class on <html>, written only by `theme.ts`, so the
  // choice can be persisted and toggled rather than following the OS blindly.
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {},
  },
  plugins: [],
};
