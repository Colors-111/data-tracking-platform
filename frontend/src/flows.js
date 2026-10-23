// frontend/src/flows.js — v1.3 资金流纯逻辑（FlowsCard/FlowsView 共用，不依赖 Vue，可单测）

// 日期位移（ISO yyyy-mm-dd）：返回新日期串；非法/畸形输入返回 null（导航按钮据此禁用）。
// new Date 会把 2026-02-30 归一化成 03-02 → 回读比对拒绝这类假日期
export function shiftDate(dateStr, days) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(dateStr ?? "")) return null;
  const d = new Date(`${dateStr}T00:00:00Z`); // 固定 UTC 解析，避免本地时区偏移日期
  if (Number.isNaN(d.getTime()) || d.toISOString().slice(0, 10) !== dateStr) return null;
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

// 已跟踪判定 → 行跳转目标：跟踪中的 code 给出其 /source/{sid}，未跟踪返回 null
// （不跳——/flows 页有"加入跟踪"）
export function flowRowTarget(code, trackedMap) {
  const sid = trackedMap?.[code];
  return sid ? `/source/${sid}` : null;
}

// code→source 映射：今日异动行自带 source_id（instrument_id=code，覆盖已注册品种）
// ∪ 自选跟踪源详情（etf-watchlist 的 cards，覆盖用户一键加入的品种）；
// 同 code 冲突时自选跟踪胜出（前端合成判定：注册后该 code 出现在自选源详情中即判
// 已跟踪；后端不做跨源归属变更，原源 instrument 保留）
export function mergeTracked(movements = [], watchlist = null) {
  const map = {};
  for (const m of movements ?? [])
    if (m?.instrument_id && m?.source_id) map[m.instrument_id] = m.source_id;
  if (watchlist?.id)
    for (const c of watchlist.cards ?? [])
      if (c?.instrument_id) map[c.instrument_id] = watchlist.id;
  return map;
}

// api.js j() 抛 `${status} ${url}`：404 → watchlist 注册时快照中无此代码
export function isNotFound(err) {
  return /^404(\s|$)/.test(err?.message ?? "");
}

// 前进一天是否可达（不越过最新快照日；ISO 串可直接字典序比较）
export function canGoNext(date, latest) {
  return !!date && !!latest && date < latest;
}

// 带符号数值：正数显式 +，零与负数不加；空值 —
export function fmtSigned(v, digits = 2) {
  if (v == null) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(digits)}`;
}

// 幅度列：带符号 + %
export function fmtPct(pct) {
  return pct == null ? "—" : `${fmtSigned(pct)}%`;
}
