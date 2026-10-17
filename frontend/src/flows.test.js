import { describe, it, expect } from "vitest";
import {
  shiftDate, flowRowTarget, mergeTracked, isNotFound, canGoNext, fmtSigned, fmtPct,
} from "./flows.js";

describe("shiftDate（/flows 日期导航）", () => {
  it("前后一天", () => {
    expect(shiftDate("2026-10-16", 1)).toBe("2026-10-17");
    expect(shiftDate("2026-10-16", -1)).toBe("2026-10-15");
  });
  it("跨月与跨年", () => {
    expect(shiftDate("2026-01-31", 1)).toBe("2026-02-01");
    expect(shiftDate("2026-03-01", -1)).toBe("2026-02-28");
    expect(shiftDate("2025-12-31", 1)).toBe("2026-01-01");
  });
  it("闰年与平年二月", () => {
    expect(shiftDate("2024-02-28", 1)).toBe("2024-02-29");
    expect(shiftDate("2025-02-28", 1)).toBe("2025-03-01");
  });
  it("非法/畸形输入返回 null（含会被 Date 归一化的假日期）", () => {
    expect(shiftDate("2026-02-30", 1)).toBeNull(); // Date 会滚到 03-02，回读比对必须拒绝
    expect(shiftDate("2026-1-5", 1)).toBeNull();
    expect(shiftDate("garbage", 1)).toBeNull();
    expect(shiftDate(null, 1)).toBeNull();
  });
});

describe("fmtSigned / fmtPct（带符号数值，红涨绿跌着色由 pctColor 负责）", () => {
  it("正带 +、负自带 -、零无符号、空值 —", () => {
    expect(fmtSigned(30.7)).toBe("+30.70");
    expect(fmtSigned(-4.8)).toBe("-4.80");
    expect(fmtSigned(0)).toBe("0.00");
    expect(fmtSigned(null)).toBe("—");
    expect(fmtSigned(101.44, 1)).toBe("+101.4");
  });
  it("幅度 = 带符号 + %", () => {
    expect(fmtPct(12)).toBe("+12.00%");
    expect(fmtPct(-1.1)).toBe("-1.10%");
    expect(fmtPct(null)).toBe("—");
  });
});

describe("已跟踪跳转（flowRowTarget / mergeTracked）", () => {
  const movements = [
    { instrument_id: "510300", source_id: "hs300-etf" },
    { instrument_id: "AU0", source_id: "gold" },
  ];
  it("已跟踪给出 /source/ 目标，未跟踪返回 null（不跳）", () => {
    const map = mergeTracked(movements, { id: "etf-watchlist", cards: [{ instrument_id: "159516" }] });
    expect(flowRowTarget("510300", map)).toBe("/source/hs300-etf");
    expect(flowRowTarget("159516", map)).toBe("/source/etf-watchlist");
    expect(flowRowTarget("588200", map)).toBeNull();
    expect(flowRowTarget("588200", null)).toBeNull();
  });
  it("冲突时自选跟踪胜出（注册后归属自选源）", () => {
    const map = mergeTracked(
      [{ instrument_id: "510300", source_id: "hs300-etf" }],
      { id: "etf-watchlist", cards: [{ instrument_id: "510300" }] });
    expect(flowRowTarget("510300", map)).toBe("/source/etf-watchlist");
  });
  it("空输入得空映射", () => {
    expect(mergeTracked([], null)).toEqual({});
    expect(mergeTracked(undefined, { id: "etf-watchlist" })).toEqual({});
  });
});

describe("isNotFound / canGoNext", () => {
  it("404 识别（加入跟踪 → 快照中无此代码）", () => {
    expect(isNotFound(new Error("404 /api/watchlist"))).toBe(true);
    expect(isNotFound(new Error("500 /api/watchlist"))).toBe(false);
    expect(isNotFound(undefined)).toBe(false);
  });
  it("前进一天不越过最新快照日", () => {
    expect(canGoNext("2026-10-15", "2026-10-16")).toBe(true);
    expect(canGoNext("2026-10-16", "2026-10-16")).toBe(false);
    expect(canGoNext(null, "2026-10-16")).toBe(false);
    expect(canGoNext("2026-10-15", null)).toBe(false);
  });
});
