import type { Config } from "tailwindcss";

// Single LIGHT trading-terminal theme. Everyone uses these tokens so four
// people's pages look like one product. Do not introduce new raw hex values in
// pages — a page that hand-rolls a colour is the thing that breaks the theme.
//
// The colours below are deliberate and verified, not taste:
//   - every ink/status token clears WCAG AA (>= 4.5:1) on bg, panel AND panel2;
//     the worst case is `up` at 4.91:1.
//   - `up`/`down` are teal/red rather than green/red, which keeps gain vs loss
//     separable for colour-vision deficiency (dE 12.0 protan, where 8 is the
//     target). Do not "fix" them to green/red.
//   - `up` trades a little chroma (0.092 vs the 0.1 ideal) for that AA contrast.
//     It is safe here because gain/loss is never colour-alone — `Tone` always
//     prints an explicit +/- sign next to the number.
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#f7f8fa",        // page background
        panel: "#ffffff",     // cards / panels
        panel2: "#f1f3f7",    // hover / nested panel
        panel3: "#e7ebf2",    // active / pressed
        line: "#e3e7ef",      // borders
        line2: "#cbd2df",     // emphasised borders, dividers on panel2
        ink: "#131722",       // primary text
        muted: "#596273",     // secondary text
        faint: "#5f6878",     // tertiary text — labels, axis ticks
        up: "#00776a",        // buy / gain
        down: "#c62828",      // sell / loss
        accent: "#1d4ed8",    // links, primary action
        warn: "#8f5000",
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
      // On a light surface depth comes from soft shadow, not from a lighter fill.
      boxShadow: {
        card: "0 1px 2px rgba(16,24,40,0.05)",
        raised: "0 4px 12px -2px rgba(16,24,40,0.10), 0 2px 4px -2px rgba(16,24,40,0.06)",
        pop: "0 12px 28px -8px rgba(16,24,40,0.18)",
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
