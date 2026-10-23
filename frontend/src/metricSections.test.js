import { describe, it, test, expect } from "vitest";
import { METRIC_SECTIONS, groupSections, groupCharts, isCommodityMeta } from "./metricSections.js";

describe("groupSections（商品源按指标分区）", () => {
  it("按 METRIC_SECTIONS 顺序分组并剔除空区（黄金源全指标；ETF 持仓提前）", () => {
    const meta = {
      "au-shfe-price": { metric: "price", unit: "元/克", name: "SHFE沪金主力" },
      "au-sge-price": { metric: "price", unit: "元/克", name: "SGE黄金现货" },
      "au-warrant": { metric: "warehouse", unit: "千克", name: "SHFE黄金仓单" },
      "au-reserve": { metric: "reserve", unit: "万盎司", name: "央行黄金储备" },
      "518880": { metric: "holding", unit: "亿份", name: "黄金ETF华安" },
      "au-cftc": { metric: "positioning", unit: "手", name: "CFTC黄金非商业净多" },
    };
    const secs = groupSections(meta);
    // 渲染顺序跟随 METRIC_SECTIONS（v1.2.1：holding 提前到 inventory/warehouse 之前），而非 meta 键序
    expect(secs.map(s => s.key)).toEqual(["price", "holding", "warehouse", "positioning", "reserve"]);
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

describe("groupCharts（区内按计价单位分图）", () => {
  const meta = {
    "au-shfe-price": { metric: "price", unit: "元/克", name: "SHFE沪金主力" },
    "au-sge-price": { metric: "price", unit: "元/克", name: "SGE黄金现货" },
    "au-london": { metric: "price", unit: "美元/盎司", name: "伦敦金现" },
  };

  it("按 unit 分组，组序=单位在区内首次出现序（人民币计价在前）", () => {
    expect(groupCharts(["au-shfe-price", "au-sge-price", "au-london"], meta)).toEqual([
      { unit: "元/克", iids: ["au-shfe-price", "au-sge-price"] },
      { unit: "美元/盎司", iids: ["au-london"] },
    ]);
  });

  it("组序跟随首现而非固定单位序：伦敦金在前时美元/盎司组在前", () => {
    expect(groupCharts(["au-london", "au-shfe-price", "au-sge-price"], meta)).toEqual([
      { unit: "美元/盎司", iids: ["au-london"] },
      { unit: "元/克", iids: ["au-shfe-price", "au-sge-price"] },
    ]);
  });

  it("空 iids 返回空组列表；meta 缺失的 iid 归入空单位组（调用方兜底显示）", () => {
    expect(groupCharts([], meta)).toEqual([]);
    expect(groupCharts(["ghost"], meta)).toEqual([{ unit: "", iids: ["ghost"] }]);
    expect(groupCharts(["au-london"], null)).toEqual([{ unit: "", iids: ["au-london"] }]);
  });

  it("同单位不拆组且组内保持 iids 键序（现货铜/铝与 SHFE 同为元/吨不受影响）", () => {
    const cu = {
      "cu-spot": { metric: "price", unit: "元/吨", name: "现货铜" },
      "cu-shfe": { metric: "price", unit: "元/吨", name: "SHFE沪铜主力" },
      "al-spot": { metric: "price", unit: "元/吨", name: "现货铝" },
    };
    expect(groupCharts(["cu-spot", "cu-shfe", "al-spot"], cu)).toEqual([
      { unit: "元/吨", iids: ["cu-spot", "cu-shfe", "al-spot"] },
    ]);
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
  it("分区顺序固定：价格→ETF持仓→库存→仓单→资金持仓→储备→融资融券 5 区（持仓类靠前）", () => {
    expect(METRIC_SECTIONS.map(s => s.key))
      .toEqual([
        "price", "holding", "inventory", "warehouse", "positioning", "reserve",
        "financing", "securities", "total", "buying", "leverage",
      ]);
  });

  it("key 唯一且 label 非空", () => {
    const keys = METRIC_SECTIONS.map(s => s.key);
    expect(new Set(keys).size).toBe(keys.length);
    expect(METRIC_SECTIONS.every(s => s.label)).toBe(true);
  });
});

describe("分区数据源说明（stale 源如实标注）", () => {
  it("inventory/positioning 分区携带 note 字段，其余分区不带", () => {
    const byKey = Object.fromEntries(METRIC_SECTIONS.map(s => [s.key, s]));
    expect(byKey.inventory.note).toContain("2026-07");
    expect(byKey.positioning.note).toContain("2021-09");
    expect(byKey.price.note).toBeUndefined();
    expect(byKey.reserve.note).toBeUndefined();
  });
  it("groupSections 输出透传 note", () => {
    const meta = { "cu-inventory": { metric: "inventory" }, "510300": { metric: "share" } };
    const secs = groupSections(meta);
    expect(secs.find(s => s.key === "inventory").note).toContain("LME");
  });
});

// ---------- v1.4 融资融券分区（设计 §5.4） ----------
// 全 11 仪器（生产 instrument_meta 全量）：8 只沪深余额/买入仪器 + 3 只全市场杠杆仪器
const MARGIN_META = {
  "margin-sh-financing": { metric: "financing", unit: "亿元" },
  "margin-sz-financing": { metric: "financing", unit: "亿元" },
  "margin-sh-securities": { metric: "securities", unit: "亿元" },
  "margin-sz-securities": { metric: "securities", unit: "亿元" },
  "margin-sh-total": { metric: "total", unit: "亿元" },
  "margin-sz-total": { metric: "total", unit: "亿元" },
  "margin-sh-buying": { metric: "buying", unit: "亿元" },
  "margin-sz-buying": { metric: "buying", unit: "亿元" },
  "margin-maintenance": { metric: "maintenance", unit: "%" },
  "margin-collateral": { metric: "collateral", unit: "亿元" },
  "margin-investors": { metric: "investors", unit: "万人" },
};

describe("v1.4 margin 分区", () => {
  test("识别为分区页（非 share 源）", () => {
    expect(isCommodityMeta(MARGIN_META)).toBe(true);
  });

  test("5 分区顺序：融资/融券/两融/买入/杠杆状况", () => {
    const secs = groupSections(MARGIN_META);
    expect(secs.map(s => s.key)).toEqual(
      ["financing", "securities", "total", "buying", "leverage"]);
  });

  test("杠杆状况区收 3 个 metric（keys 多值）", () => {
    const lev = groupSections(MARGIN_META).find(s => s.key === "leverage");
    expect(lev.iids).toEqual(["margin-maintenance", "margin-collateral", "margin-investors"]);
  });

  test("杠杆状况区内按单位分 3 图（%/亿元/万人 不同图）", () => {
    const lev = groupSections(MARGIN_META).find(s => s.key === "leverage");
    const groups = groupCharts(lev.iids, MARGIN_META);
    expect(groups.map(g => g.unit)).toEqual(["%", "亿元", "万人"]);
  });

  test("余额区沪+深同图（同为亿元不拆）", () => {
    const fin = groupSections(MARGIN_META).find(s => s.key === "financing");
    const groups = groupCharts(fin.iids, MARGIN_META);
    expect(groups).toHaveLength(1);
    expect(groups[0].iids).toEqual(["margin-sh-financing", "margin-sz-financing"]);
  });

  test("既有商品源分区不回归（leverage 等 margin metric 不混入商品源页面）", () => {
    const goldMeta = { "au-shfe-price": { metric: "price", unit: "元/克" } };
    expect(groupSections(goldMeta).map(s => s.key)).toEqual(["price"]);
  });
});

describe("METRIC_SECTIONS 完整性", () => {
  it("metric 引用无重复（防同一指标在两个分区重复渲染）", () => {
    const all = METRIC_SECTIONS.flatMap(s => (s.keys ?? [s.key]));
    expect(new Set(all).size).toBe(all.length);
  });
});
