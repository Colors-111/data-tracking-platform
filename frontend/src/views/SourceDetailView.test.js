// @vitest-environment happy-dom
// v1.3.x ETF 类别总量曲线组件级测试：原值模式图例最前叠"全类总额"（虚线）+
// "已跟踪合计"（点线）；banner 覆盖率徽章 <90% 黄 / ≥90% 绿；统一序列
// （归一化/对比）模式不叠特殊线（量纲不同）；category_total 为空不渲染。
import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import SourceDetailView from "./SourceDetailView.vue";

vi.mock("../api", () => ({
  api: { sourceDetail: vi.fn(), series: vi.fn(), jobs: vi.fn(), sources: vi.fn() },
}));
import { api } from "../api";

const charts = []; // TrendChart stub 捕获的 props（引用同一 props 对象，断言时取最新 series）
const TrendChartStub = {
  props: ["series", "percent", "unit"],
  setup(props) { charts.push(props); return {}; },
  template: "<div class='trend-stub' />",
};

const DETAIL = {
  id: "hs300-etf", name: "沪深300 ETF", unit: "亿份", as_of_date: "2026-10-15",
  cards: [{ instrument_id: "510300", name: "沪深300ETF华泰柏瑞", code: "510300",
            metric: "share", unit: "亿份", value: 103, day_change: 3, day_pct: 3.0,
            range_change: 3, range_pct: 3.0 }],
  series: { "510300": [["2026-10-14", 100], ["2026-10-15", 103]] },
  instrument_meta: { "510300": { metric: "share", unit: "亿份", name: "沪深300ETF华泰柏瑞" } },
  category_total: [["2026-10-14", 180], ["2026-10-15", 200]],
  tracked_total: [["2026-10-14", 100], ["2026-10-15", 103]],
  coverage: 51.5,
};

beforeEach(() => { vi.clearAllMocks(); charts.length = 0; });

const mountLoaded = async (detail = DETAIL) => {
  api.sourceDetail.mockResolvedValue(detail);
  const w = mount(SourceDetailView, {
    props: { id: "hs300-etf" },
    global: { stubs: {
      TrendChart: TrendChartStub, JobStatus: true, CardRow: true, CompareChips: true,
    } },
  });
  await flushPromises();
  return w;
};

describe("SourceDetailView ETF 类别总量曲线", () => {
  it("原值模式：全类总额（虚线）/已跟踪合计（点线）置图例最前，后接成分线", async () => {
    await mountLoaded();
    const s = charts[0].series;
    expect(s.map(x => x.name)).toEqual(["全类总额", "已跟踪合计", "沪深300ETF华泰柏瑞"]);
    expect(s.map(x => x.lineType)).toEqual(["dashed", "dotted", undefined]);
  });

  it("category_total/tracked_total 为空：不渲染两条特殊线，仅成分线", async () => {
    await mountLoaded({ ...DETAIL, category_total: [], tracked_total: [], coverage: null });
    expect(charts[0].series.map(x => x.name)).toEqual(["沪深300ETF华泰柏瑞"]);
    expect(charts[0].series[0].lineType).toBeUndefined();
  });

  it("覆盖率徽章：<90% 黄、≥90% 绿、coverage 缺失不渲染", async () => {
    const w = await mountLoaded();
    expect(w.find(".coverage-badge.warn").text()).toContain("覆盖该类 51.5%");
    const w2 = await mountLoaded({ ...DETAIL, coverage: 93.8 });
    expect(w2.find(".coverage-badge.ok").text()).toContain("覆盖该类 93.8%");
    const w3 = await mountLoaded({ ...DETAIL, coverage: null,
                                   category_total: [], tracked_total: [] });
    expect(w3.find(".coverage-badge").exists()).toBe(false);
  });

  it("归一化统一序列模式：不叠特殊线（特殊线未参与 /api/series 归一化）", async () => {
    api.series.mockResolvedValue({ series: [
      { instrument_id: "510300", name: "沪深300ETF华泰柏瑞",
        points: [["2026-10-14", 100], ["2026-10-15", 103]] }] });
    const w = await mountLoaded();
    await w.find("input[type=checkbox]").setValue(true); // 归一化开关
    await flushPromises();
    expect(api.series).toHaveBeenCalled();
    expect(charts[0].series.map(x => x.name)).toEqual(["沪深300ETF华泰柏瑞"]);
  });
});
