import type { Config } from "tailwindcss";

// Single dark trading-terminal theme. Everyone uses these tokens so four people's
// pages look like one product. Do not introduce new raw hex values in pages.
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#0b0e14",        // page background
        panel: "#11151f",     // cards / panels
        panel2: "#161b28",    // hover / nested panel
        line: "#1f2637",      // borders
        ink: "#e6e9f0",       // primary text
        muted: "#8b93a7",     // secondary text
        up: "#26a69a",        // buy / gain
        down: "#ef5350",      // sell / loss
        accent: "#4c8dff",    // links, primary action
        warn: "#f0b429",
      },
      fontFamily: {
        sans: ["ui-sans-serif", "system-ui", "Segoe UI", "Roboto", "sans-serif"],
        mono: ["ui-monospace", "Consolas", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
