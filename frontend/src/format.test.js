import { describe, it, expect } from "vitest";
import { pctColor, sparkBars, fmtVal, fmtChange } from "./format.js";

describe("pctColor（红涨绿跌）", () => {
  it("正数涨红、负数跌绿、零灰", () => {
    expect(pctColor(1.2)).toBe("up");
    expect(pctColor(-0.5)).toBe("down");
    expect(pctColor(0)).toBe("flat");
  });
});

describe("sparkBars（5日迷你趋势）", () => {
  it("映射为块状字符", () => {
    expect(sparkBars([1, 2, 3, 4, 5])).toBe("▁▃▄▆█");
    expect(sparkBars([])).toBe("");
  });
  it("全等值不除零", () => {
    expect(sparkBars([2, 2, 2])).toBe("▄▄▄");
  });
});

describe("fmtVal / fmtChange", () => {
  it("亿份保留两位", () => {
    expect(fmtVal(254.849, "亿份")).toBe("254.85 亿份");
  });
  it("变化带符号", () => {
    expect(fmtChange(7.95, 3.22)).toBe("+7.95 (+3.22%)");
    expect(fmtChange(-0.18, -2.69)).toBe("-0.18 (-2.69%)");
  });
});
