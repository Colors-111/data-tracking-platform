// UP/DOWN/FLAT 与 App.vue :root 保持同步（canvas 取不到 CSS 变量）
export const UP = "#e74c3c", DOWN = "#27ae60", FLAT = "#95a5a6";

export function pctColor(pct) {
  if (pct > 0) return "up";
  if (pct < 0) return "down";
  return "flat";
}

const BARS = "▁▂▃▄▅▆▇█";
export function sparkBars(vals) {
  if (!vals || vals.length === 0) return "";
  const min = Math.min(...vals), max = Math.max(...vals);
  const span = max - min;
  return vals.map(v => {
    // 等值（span=0）取中位 0.5，避免平坦序列误显示为全最低柱
    const t = span ? (v - min) / span : 0.5;
    // half-down 取整（3.5→3）：块位分布更均匀（▁▃▄▆█ 而非 ▁▃▅▆█）
    return BARS[Math.ceil(t * (BARS.length - 1) - 0.5)];
  }).join("");
}

export function fmtVal(v, unit) {
  if (v == null) return "—";
  const n = Math.abs(v) >= 1000 ? v.toLocaleString("zh-CN", { maximumFractionDigits: 0 })
                                : v.toFixed(2);
  return `${n} ${unit ?? ""}`.trim();
}

export function fmtChange(change, pct) {
  if (change == null) return "—";
  const sign = change > 0 ? "+" : "";
  return `${sign}${change.toFixed(2)} (${sign}${(pct ?? 0).toFixed(2)}%)`;
}
