import { describe, it, expect } from "vitest";
import { METRIC_SECTIONS, groupSections, isCommodityMeta } from "./metricSections.js";

describe("groupSections（商品源按指标分区）", () => {
  it("按 METRIC_SECTIONS 顺序分组并剔除空区（黄金源全指标）", () => {
    const meta = {
      "au-shfe-price": { metric: "price", unit: "元/克", name: "SHFE沪金主力" },
      "au-sge-price": { metric: "price", unit: "元/克", name: "SGE黄金现货" },
      "au-warrant": { metric: "warehouse", unit: "千克", name: "SHFE黄金仓单" },
      "au-reserve": { metric: "reserve", unit: "万盎司", name: "央行黄金储备" },
      "518880": { metric: "holding", unit: "亿份", name: "黄金ETF华安" },
      "au-cftc": { metric: "positioning", unit: "手", name: "CFTC黄金非商业净多" },
    };
    const secs = groupSections(meta);
    // 渲染顺序跟随 METRIC_SECTIONS，而非 meta 键序
    expect(secs.map(s => s.key)).toEqual(["price", "warehouse", "positioning", "reserve", "holding"]);
    expect(secs.find(s => s.key === "price").iids).toEqual(["au-shfe-price", "au-sge-price"]);
    expect(secs.find(s => s.key === "holding").iids).toEqual(["518880"]);
  });

  it("铜源：库存区聚合 SHFE+LME 两组 instrument", () => {
    const meta = {
      "cu-shfe-price": { metric: "price", unit: "元/吨", name: "SHFE沪铜主力" },
      "cu-inventory": { metric: "inventory", unit: "吨", name: "SHFE电解铜库存" },
      "cu-warrant": { metric: "warehouse", unit: "吨", name: "SHFE铜仓单" },
      "cu-lme-stock": { metric: "inventory", unit: "吨", name: "LME铜库存" },
      "cu-lme-holding": { metric: "positioning", unit: "张", name: "LME铜净持仓" },
    };
    const byKey = Object.fromEntries(groupSections(meta).map(s => [s.key, s.iids]));
    expect(byKey.inventory).toEqual(["cu-inventory", "cu-lme-stock"]);
    expect(byKey.price).toEqual(["cu-shfe-price"]);
    expect(byKey.warehouse).toEqual(["cu-warrant"]);
    expect(byKey.positioning).toEqual(["cu-lme-holding"]);
  });

  it("share 与空 meta 不进任何区（ETF 源走旧布局）", () => {
    expect(groupSections({ "510300": { metric: "share", unit: "亿份", name: "沪深300ETF华泰柏瑞" } })).toEqual([]);
    expect(groupSections({})).toEqual([]);
    expect(groupSections(null)).toEqual([]);
  });
});

describe("isCommodityMeta（新旧布局切换）", () => {
  it("含任一非 share metric 即商品源；全 share 为 ETF 源", () => {
    expect(isCommodityMeta({ a: { metric: "share" }, b: { metric: "price" } })).toBe(true);
    expect(isCommodityMeta({ "510300": { metric: "share" } })).toBe(false);
    expect(isCommodityMeta(null)).toBe(false);
    expect(isCommodityMeta({})).toBe(false);
  });
});

describe("METRIC_SECTIONS 定义", () => {
  it("key 唯一且 label 非空", () => {
    const keys = METRIC_SECTIONS.map(s => s.key);
    expect(new Set(keys).size).toBe(keys.length);
    expect(METRIC_SECTIONS.every(s => s.label)).toBe(true);
  });
});
