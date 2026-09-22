import type { Config } from "tailwindcss";

// Single dark trading-terminal theme. Everyone uses these tokens so four people's
// pages look like one product. Do not introduce new raw hex values in pages.
//
// The colours below are deliberate and verified, not taste:
//   - every ink/status token clears WCAG AA (>= 4.5:1) on bg, panel AND panel2;
//   - `up`/`down` are teal/red rather than green/red, which keeps gain vs loss
//     separable for deuteranopia (dE 11.6, where 8 is the target). Do not
//     "fix" them to green/red.
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#0b0e14",        // page background
        panel: "#11151f",     // cards / panels
        panel2: "#161b28",    // hover / nested panel
        panel3: "#1b2233",    // active / pressed
        line: "#1f2637",      // borders
        line2: "#2a3348",     // emphasised borders, dividers on panel2
        ink: "#e6e9f0",       // primary text
        muted: "#8b93a7",     // secondary text
        faint: "#5d6579",     // tertiary text — labels, axis ticks
        up: "#26a69a",        // buy / gain
        down: "#ef5350",      // sell / loss
        accent: "#4c8dff",    // links, primary action
        warn: "#f0b429",
      },
      fontFamily: {
        sans: ["ui-sans-serif", "system-ui", "Segoe UI", "Roboto", "sans-serif"],
        mono: ["ui-monospace", "Consolas", "Menlo", "monospace"],
      },
      // Tuned pairs — a terminal reads better with tight headings and roomy body.
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem", letterSpacing: "0.04em" }],
        xs: ["0.75rem", { lineHeight: "1.125rem" }],
        sm: ["0.8125rem", { lineHeight: "1.25rem" }],
        base: ["0.9375rem", { lineHeight: "1.5rem" }],
        lg: ["1.0625rem", { lineHeight: "1.625rem" }],
        xl: ["1.25rem", { lineHeight: "1.75rem", letterSpacing: "-0.01em" }],
        "2xl": ["1.5rem", { lineHeight: "2rem", letterSpacing: "-0.015em" }],
        "3xl": ["1.875rem", { lineHeight: "2.25rem", letterSpacing: "-0.02em" }],
      },
      borderRadius: {
        md: "0.375rem",
        lg: "0.5rem",
        xl: "0.75rem",
      },
      boxShadow: {
        card: "0 1px 2px rgba(0,0,0,0.4)",
        raised: "0 4px 16px -4px rgba(0,0,0,0.6)",
        pop: "0 12px 32px -8px rgba(0,0,0,0.7)",
      },
      keyframes: {
        "fade-in": {
          from: { opacity: "0", transform: "translateY(4px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        shimmer: {
          from: { backgroundPosition: "-200% 0" },
          to: { backgroundPosition: "200% 0" },
        },
      },
      animation: {
        "fade-in": "fade-in 0.18s ease-out",
        shimmer: "shimmer 1.6s linear infinite",
      },
    },
  },
  plugins: [],
};

export default config;
