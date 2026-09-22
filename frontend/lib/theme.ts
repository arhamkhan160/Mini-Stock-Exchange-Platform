// Canvas libraries (lightweight-charts) cannot read Tailwind classes, so the few
// colours the chart needs live here as literals.
//
// These MIRROR tailwind.config.ts. If you change a token there, change it here —
// they are the same design decision expressed twice because two renderers need it.
export const chartTheme = {
  panel: "#11151f",   // colors.panel   — chart background
  line: "#1f2637",    // colors.line    — horizontal grid
  line2: "#2a3348",   // colors.line2   — crosshair
  muted: "#8b93a7",   // colors.muted   — axis text
  up: "#26a69a",      // colors.up      — bullish candle
  down: "#ef5350",    // colors.down    — bearish candle
} as const;
