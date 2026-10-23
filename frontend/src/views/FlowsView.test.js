// @vitest-environment happy-dom
// 组件级点击测试：纯函数测试盖不住模板事件绑定（审查教训：addTracking(r.code) 传参错
// 位导致 POST body 变 {}，28 例纯函数测试全绿也发现不了）。mock api 模块挂载真实组件。
import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import FlowsView from "./FlowsView.vue";

vi.mock("../api", () => ({
  api: { flows: vi.fn(), watchlistAdd: vi.fn() },
}));
import { api } from "../api";

const FLOWS = {
  as_of_date: "2026-10-16", total_change: 101.4, count: 1678,
  top_in: [{ code: "510300", name: "300ETF", exchange: "SSE", shares: 285.5, change: 30.7, pct: 12.0 }],
  top_out: [{ code: "588200", name: "科创芯片", exchange: "SSE", shares: 446.7, change: -4.8, pct: -1.1 }],
};

beforeEach(() => vi.clearAllMocks());

const mountLoaded = async (flowsRes = FLOWS) => {
  api.flows.mockResolvedValue(flowsRes);
  const w = mount(FlowsView);
  await flushPromises();
  return w;
};

describe("FlowsView 加入跟踪（组件级点击）", () => {
  it("点击行按钮 → POST 收到行对象解出的 code，成功后按钮变已跟踪 ✓（禁用）", async () => {
    api.watchlistAdd.mockResolvedValue({ registered: true, name: "300ETF", history_rows: 2862 });
    const w = await mountLoaded();

    expect(api.flows).toHaveBeenCalledWith(null, 15);
    await w.find("button.add-btn").trigger("click");
    await flushPromises();

    // 回归守卫：传行对象 r 而非 r.code——错位时这里收到 undefined（body 变 {}，后端 422）
    expect(api.watchlistAdd).toHaveBeenCalledTimes(1);
    expect(api.watchlistAdd).toHaveBeenCalledWith("510300");

    expect(w.find("button.done").exists()).toBe(true);
    expect(w.find("button.done").attributes("disabled")).toBeDefined();
    expect(w.find("button.done").text()).toContain("已跟踪 ✓");
    expect(w.findAll("button.add-btn")).toHaveLength(1); // 赎回榜行不受影响
  });

  it("404 → 行内提示'快照中无此代码'，按钮保持可加", async () => {
    api.watchlistAdd.mockRejectedValue(new Error("404 /api/watchlist"));
    const w = await mountLoaded();

    await w.find("button.add-btn").trigger("click");
    await flushPromises();

    expect(w.text()).toContain("快照中无此代码");
    expect(w.findAll("button.add-btn")).toHaveLength(2);
    expect(w.find("button.done").exists()).toBe(false);
  });

  it("日期导航：前一天请求带 date 参数，最新日'后一天'禁用", async () => {
    const w = await mountLoaded();

    const nextBtn = w.findAll("button").find(b => b.text().includes("后一天"));
    expect(nextBtn.attributes("disabled")).toBeDefined(); // as_of_date 即上界

    const prevBtn = w.findAll("button").find(b => b.text().includes("前一天"));
    api.flows.mockResolvedValue({ ...FLOWS, as_of_date: "2026-10-15" });
    await prevBtn.trigger("click");
    await flushPromises();

    expect(api.flows).toHaveBeenLastCalledWith("2026-10-15", 15);
    expect(w.text()).toContain("快照日 2026-10-15");
  });

  it("无数据日期显示空态", async () => {
    const w = await mountLoaded({ ...FLOWS, as_of_date: "2026-10-14", total_change: 0, count: 0, top_in: [], top_out: [] });
    expect(w.text()).toContain("该日期无快照数据");
  });
});
