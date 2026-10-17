// @vitest-environment happy-dom
// FlowsCard 关键交互：已跟踪行渲染为可点 RouterLink（<a>），未跟踪行为纯 span；
// 已跟踪映射 = movements prop ∪ etf-watchlist 源详情（mock api.sourceDetail）
import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import FlowsCard from "./FlowsCard.vue";

vi.mock("../api", () => ({
  api: { flows: vi.fn(), sourceDetail: vi.fn() },
}));
import { api } from "../api";

const CARD_FLOWS = {
  as_of_date: "2026-10-16", total_change: 101.4, count: 1678,
  top_in: [
    { code: "510300", name: "300ETF", exchange: "SSE", shares: 285.5, change: 30.7, pct: 12.0 },
    { code: "159516", name: "芯片ETF", exchange: "SZSE", shares: 120.1, change: 18.2, pct: 17.9 },
    { code: "588200", name: "科创芯片", exchange: "SSE", shares: 446.7, change: 9.9, pct: 2.3 },
  ],
  top_out: [
    { code: "159920", name: "恒生ETF", exchange: "SZSE", shares: 88.4, change: -6.1, pct: -6.5 },
  ],
};

beforeEach(() => vi.clearAllMocks());

async function mountCard() {
  api.flows.mockResolvedValue(CARD_FLOWS);
  api.sourceDetail.mockResolvedValue({ id: "etf-watchlist", cards: [{ instrument_id: "159516" }] });
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div/>" } },
      { path: "/source/:id", component: { template: "<div/>" } },
      { path: "/flows", component: { template: "<div/>" } }, // a.more 入口，不注册则 RouterLink 告警
    ],
  });
  // isReady() 不在 mount 前调用：初始导航由 app.use(router) 触发，此前 await 会死等
  const w = mount(FlowsCard, {
    global: { plugins: [router] },
    props: { movements: [{ instrument_id: "510300", source_id: "hs300-etf" }] },
  });
  await flushPromises();
  return w;
}

describe("FlowsCard 资金异动卡", () => {
  it("摘要行：净流入合计（带符号）+ 覆盖只数 + 快照日徽章", async () => {
    const w = await mountCard();
    expect(w.text()).toContain("+101.4");
    expect(w.text()).toContain("覆盖 1678 只");
    expect(w.text()).toContain("2026-10-16");
  });

  it("已跟踪行（movements ∪ 自选源）渲染为源页 <a>，未跟踪行为 span 不跳", async () => {
    const w = await mountCard();
    const links = w.findAll("a.f-row");
    expect(links).toHaveLength(2); // 510300（movements 判定）+ 159516（自选源判定）
    const hrefs = links.map(a => a.attributes("href"));
    expect(hrefs.some(h => h?.includes("/source/hs300-etf"))).toBe(true);
    expect(hrefs.some(h => h?.includes("/source/etf-watchlist"))).toBe(true);
    // 588200 与 159920 未跟踪 → span（不渲染成链接），整卡链接里也不含它们的源页
    expect(w.findAll("span.f-row")).toHaveLength(2);
    expect(hrefs.join(" ")).not.toContain("588200");
    expect(w.find("a.more").attributes("href")).toBe("/flows"); // 底部入口仍在
  });

  it("请求失败 → 卡内局部错误提示，不抛出阻断", async () => {
    api.flows.mockRejectedValue(new Error("500 /api/flows"));
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: "/", component: { template: "<div/>" } },
        { path: "/flows", component: { template: "<div/>" } },
      ],
    });
    const w = mount(FlowsCard, { global: { plugins: [router] }, props: { movements: [] } });
    await flushPromises();
    expect(w.text()).toContain("全市场资金流加载失败");
  });
});
