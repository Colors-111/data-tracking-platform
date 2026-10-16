// v1.2 商品组源详情页按指标分区（设计 §5.2/§8）。
// 纯函数模块：METRIC_SECTIONS + 分组/判定逻辑，供 SourceDetailView 与 Vitest 复用。
// 顺序即渲染顺序；share 不在列表——全 share 的 ETF 源走旧单区布局（Task 14/17 契约不动）。
export const METRIC_SECTIONS = [
  { key: "price", label: "价格" },       // 期货/现货/SGE/伦敦金 同区叠加（跨单位归一化后可比）
  { key: "inventory", label: "库存" },
  { key: "warehouse", label: "仓单" },
  { key: "positioning", label: "资金·持仓" },
  { key: "reserve", label: "储备" },
  { key: "holding", label: "ETF 持仓" },
];

// 商品源判定：任一 instrument 的 metric 非 share 即商品源（share-only 源保持旧布局）
export function isCommodityMeta(meta) {
  return Object.values(meta ?? {}).some(m => m && m.metric !== "share");
}

// 按 METRIC_SECTIONS 顺序把 instrument id 归入各区，空区剔除。
// share 与未登记 metric 不进任何区（前者走旧布局；后者属新增指标，需同步扩区）。
// iids 保持 instrument_meta 的键序（API 按 sort_order 构造）。
export function groupSections(meta) {
  return METRIC_SECTIONS
    .map(s => ({ ...s, iids: Object.keys(meta ?? {}).filter(iid => meta[iid].metric === s.key) }))
    .filter(s => s.iids.length);
}
