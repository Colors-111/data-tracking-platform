// v1.2 商品组源详情页按指标分区（设计 §5.2/§8）；v1.2.1：ETF 持仓区提前——
// 资金与持仓类靠前、库存/仓单类靠后是研判习惯。
// 纯函数模块：METRIC_SECTIONS + 分组/判定逻辑，供 SourceDetailView 与 Vitest 复用。
// 顺序即渲染顺序；share 不在列表——全 share 的 ETF 源走旧单区布局（Task 14/17 契约不动）。
export const METRIC_SECTIONS = [
  { key: "price", label: "价格" },       // 期货/现货/SGE/伦敦金 同区；区内按计价单位分图（元/克 ≠ 美元/盎司）
  { key: "holding", label: "ETF 持仓" }, // 含场内 ETF 份额与 SPDR 全球金 ETF 吨持仓（holding/吨）
  { key: "inventory", label: "库存", note: "LME 库存源停更于 2026-07；SHFE 库存源仅保留近 60 个交易日（每日采集中）" },
  { key: "warehouse", label: "仓单" },
  { key: "positioning", label: "资金·持仓", note: "LME 净持仓源停更于 2021-09（历史数据仍可查）" },
  { key: "reserve", label: "储备" },
  // ---- v1.4 融资融券（设计 §5.4）：余额类沪/深两线一图，杠杆状况区 3 metric 按单位分 3 图 ----
  { key: "financing", label: "融资余额" },
  { key: "securities", label: "融券余额" },
  { key: "total", label: "两融余额", note: "融资余额+融券余额，沪深交易所口径；全市场口径见杠杆状况区" },
  { key: "buying", label: "融资买入额" },
  { key: "leverage", label: "杠杆状况",
    keys: ["maintenance", "collateral", "investors"],  // 多 metric 同区（keys 数组）
    note: "全市场两融账户口径（含北交所）；担保比例预警线 130%，担保物与投资者数 2015 年前后起有值" },
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
    .map(s => {
      const keys = s.keys ?? [s.key];   // 多 metric 区（如 leverage）声明 keys 数组
      return { ...s, keys,
               iids: Object.keys(meta ?? {}).filter(iid => keys.includes(meta[iid].metric)) };
    })
    .filter(s => s.iids.length);
}

// 区内按计价单位分图（v1.2.1）：返回 [{unit, iids}]，组序按 unit 在 iids 中首次出现序
// （非固定单位序——区内的主计价单位排前）。同单位不拆组、组内保持 iids 键序
// （现货铜/铝与 SHFE 同为 元/吨 时仍合一张图）。meta 缺失该 iid 或无 unit 时归入 ""
// 组（调用方负责兜底显示）。
export function groupCharts(iids, meta) {
  const groups = [];
  const byUnit = new Map();
  for (const iid of iids ?? []) {
    const unit = meta?.[iid]?.unit ?? "";
    if (!byUnit.has(unit)) {
      const list = [];
      byUnit.set(unit, list);
      groups.push({ unit, iids: list });
    }
    byUnit.get(unit).push(iid);
  }
  return groups;
}
