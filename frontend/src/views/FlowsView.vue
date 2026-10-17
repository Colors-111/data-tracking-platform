<!-- frontend/src/views/FlowsView.vue — v1.3 /flows 全市场资金流榜（日期导航 + 申购/赎回双榜 + 一键加入跟踪） -->
<script setup>
import { ref, onMounted } from "vue";
import { api } from "../api";
import { fmtVal, pctColor } from "../format.js";
import { shiftDate, canGoNext, isNotFound, fmtSigned, fmtPct } from "../flows.js";

const LIMIT = 15;

const flows = ref(null);
const error = ref("");
const date = ref(null);   // 当前查看日（null=最新）；显式请求的无数据日期保持该日期 → 显示空态
const latest = ref(null); // 最新快照日（"后一天"边界，防止点击进无数据的未来）
const tracked = ref(new Set()); // v1.3 简化：已跟踪为内存态，刷新还原（不查后端）
const busy = ref(new Set());    // POST 在途的 code，防重复点击
const rowMsg = ref({});         // code → 行内提示（如"快照中无此代码"）

async function load(d) {
  error.value = "";
  flows.value = null; // 切换日期时立即清空，防旧数据闪现（同 SourceDetailView 约定）
  try {
    const res = await api.flows(d, LIMIT);
    flows.value = res;
    date.value = d ?? res.as_of_date;
    if (!d) latest.value = res.as_of_date;
  } catch (e) {
    error.value = e?.message ?? String(e); // Task 13 约定：view 内 catch → 主区 .banner
  }
}
onMounted(() => load(null));

const prev = () => load(shiftDate(date.value, -1));
const next = () => load(shiftDate(date.value, 1));

async function addTracking(row) {
  if (tracked.value.has(row.code) || busy.value.has(row.code)) return;
  busy.value.add(row.code);
  try {
    await api.watchlistAdd(row.code);
    tracked.value.add(row.code); // ref 包裹的 Set 为响应式，add 触发更新
    if (rowMsg.value[row.code]) delete rowMsg.value[row.code];
  } catch (e) {
    rowMsg.value = { ...rowMsg.value,
      [row.code]: isNotFound(e) ? "快照中无此代码" : `加入失败：${e?.message ?? e}` };
  } finally {
    busy.value.delete(row.code);
  }
}

// 双榜共用表结构：净申购为正变动（红），净赎回为负变动（绿），配色随行数据 pctColor 走
const sides = [
  { key: "in", label: "申购榜", cls: "up", empty: "该日无净申购", rowsOf: (f) => f.top_in },
  { key: "out", label: "赎回榜", cls: "down", empty: "该日无净赎回", rowsOf: (f) => f.top_out },
];
</script>

<template>
  <div class="banner error" v-if="error">加载失败：{{ error }}</div>
  <div v-else-if="!date" class="card">资金流加载中…</div>
  <template v-else>
    <div class="banner">全市场资金异动<span class="badge">快照日 {{ date }}</span></div>

    <div class="date-bar">
      <button @click="prev">← 前一天</button>
      <span class="cur">{{ date }}</span>
      <button @click="next" :disabled="!canGoNext(date, latest)">后一天 →</button>
    </div>

    <div v-if="!flows" class="card">榜单加载中…</div>
    <template v-else>
      <p class="flow-summary">
        净流入合计 <b :class="pctColor(flows.total_change)">{{ fmtSigned(flows.total_change, 1) }}</b> 亿份
        · 覆盖 {{ flows.count }} 只
      </p>

      <div v-if="!flows.top_in?.length && !flows.top_out?.length" class="card empty">
        该日期无快照数据
      </div>
      <div v-else class="tables">
        <section v-for="s in sides" :key="s.key" class="card">
          <h3 :class="s.cls">{{ s.label }}<small>TOP {{ LIMIT }}</small></h3>
          <table v-if="s.rowsOf(flows)?.length" class="flow-table">
            <thead><tr>
              <th>代码</th><th>名称</th><th>交易所</th>
              <th class="num">最新份额</th><th class="num">变动</th><th class="num">幅度</th><th></th>
            </tr></thead>
            <tbody>
              <tr v-for="r in s.rowsOf(flows)" :key="r.code">
                <td class="mono">{{ r.code }}</td>
                <td>{{ r.name }}</td>
                <td class="exch">{{ r.exchange }}</td>
                <td class="num">{{ fmtVal(r.shares, "亿份") }}</td>
                <td class="num" :class="pctColor(r.change)">{{ fmtSigned(r.change) }}</td>
                <td class="num" :class="pctColor(r.change)">{{ fmtPct(r.pct) }}</td>
                <td class="act">
                  <button v-if="!tracked.has(r.code)" class="add-btn"
                          :disabled="busy.has(r.code)" @click="addTracking(r.code)">➕ 加入跟踪</button>
                  <button v-else class="done" disabled>已跟踪 ✓</button>
                  <span v-if="rowMsg[r.code]" class="row-msg">{{ rowMsg[r.code] }}</span>
                </td>
              </tr>
            </tbody>
          </table>
          <p v-else class="none">{{ s.empty }}</p>
        </section>
      </div>

      <p class="hint">加入跟踪后，该品种将出现在侧边栏"自选跟踪"分组，并自动从快照回填全部历史；"已跟踪"标记为本页内存态，刷新后还原。</p>
    </template>
  </template>
</template>

<style scoped>
.banner.error { background:#7f8c8d; }   /* 同 OverviewView：错误态用中性色，别与正常红色 banner 混淆 */
.date-bar { display:flex; align-items:center; gap:10px; margin:12px 0 4px; }
.date-bar button { border:1px solid #ddd; background:#fff; border-radius:4px; padding:4px 12px; cursor:pointer; font-size:12px; }
.date-bar button:disabled { color:#bbb; cursor:default; }
.cur { font-weight:700; font-size:13px; font-variant-numeric: tabular-nums; }
.flow-summary { margin:10px 2px; font-size:13px; color:#555; }
.flow-summary b { font-size:15px; font-variant-numeric: tabular-nums; }
.tables { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
@media (max-width: 900px) { .tables { grid-template-columns:1fr; } }
h3 { margin:0 0 8px; font-size:14px; }
h3 small { color:#999; font-size:11px; font-weight:400; margin-left:8px; }
.flow-table { width:100%; border-collapse:collapse; font-size:12.5px; }
.flow-table th { color:#999; font-weight:400; text-align:left; padding:6px 6px; border-bottom:1px solid #eee; white-space:nowrap; }
.flow-table td { padding:7px 6px; border-bottom:1px solid #f3f3f3; }
.num { text-align:right; font-variant-numeric: tabular-nums; }
.mono { font-variant-numeric: tabular-nums; }
.exch { color:#999; font-size:11px; }
.act { text-align:right; white-space:nowrap; }
.add-btn { border:1px solid #c0392b; color:#c0392b; background:#fff; border-radius:4px; padding:2px 8px; font-size:11px; cursor:pointer; }
.add-btn:hover:not(:disabled) { background:#fdf0ee; }
.add-btn:disabled { opacity:.6; cursor:default; }
.done { border:1px solid #ddd; color:#999; background:#fafafa; border-radius:4px; padding:2px 8px; font-size:11px; }
.row-msg { display:block; color:#c0392b; font-size:10px; margin-top:2px; }
.empty { color:#999; text-align:center; padding:40px; }
.none { color:#999; text-align:center; padding:20px; margin:0; }
.hint { color:#999; font-size:12px; margin:12px 2px 0; }
</style>
