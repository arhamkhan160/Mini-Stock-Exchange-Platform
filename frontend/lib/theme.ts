// Canvas libraries (lightweight-charts) cannot read Tailwind classes, so the few
// colours the chart needs live here as literals.
//
// These MIRROR tailwind.config.ts. If you change a token there, change it here —
// they are the same design decision expressed twice because two renderers need it.
export const chartTheme = {
  panel: "#ffffff",   // colors.panel   — chart background
  line: "#e3e7ef",    // colors.line    — horizontal grid
  line2: "#cbd2df",   // colors.line2   — crosshair
  muted: "#596273",   // colors.muted   — axis text
  up: "#00776a",      // colors.up      — bullish candle
  down: "#c62828",    // colors.down    — bearish candle
} as const;
