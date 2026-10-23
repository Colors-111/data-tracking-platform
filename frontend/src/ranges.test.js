import { describe, it, expect } from "vitest";
import { RANGES, validateRange, rangeParams } from "./ranges.js";

describe("RANGES", () => {
  it("7 个后端档位 + custom 收尾（顺序即时间条渲染顺序）", () => {
    expect(RANGES.map(r => r[0]))
      .toEqual(["1m", "3m", "6m", "1y", "3y", "5y", "all", "custom"]);
    expect(RANGES.every(r => r[1])).toBe(true);
  });
});

describe("validateRange（自定义起止校验）", () => {
  it("合法起止返回 null（含同日边界）", () => {
    expect(validateRange("2021-10-17", "2026-10-17")).toBeNull();
    expect(validateRange("2026-10-17", "2026-10-17")).toBeNull();
  });

  it("非法格式返回错误信息（非空字符串）", () => {
    expect(validateRange("banana", "2026-10-17")).toBeTruthy();
    expect(validateRange("2021-10-17", "")).toBeTruthy();
    expect(validateRange("", "")).toBeTruthy();
    expect(validateRange(undefined, "2026-10-17")).toBeTruthy();
    expect(validateRange("2021-10-7", "2026-10-17")).toBeTruthy();   // 缺零
    expect(validateRange("2021/10/17", "2026-10-17")).toBeTruthy();  // 分隔符
  });

  it("start 晚于 end 返回错误信息", () => {
    expect(validateRange("2026-01-01", "2020-01-01")).toBeTruthy();
  });
});

describe("rangeParams（请求参数构造）", () => {
  it("custom 模式返回 start/end，不带 range", () => {
    expect(rangeParams("custom", "2021-01-01", "2026-01-01"))
      .toEqual({ start: "2021-01-01", end: "2026-01-01" });
  });

  it("普通档位返回 range，不带 start/end", () => {
    expect(rangeParams("3m", "2021-01-01", "2026-01-01")).toEqual({ range: "3m" });
    expect(rangeParams("all")).toEqual({ range: "all" });
  });
});
