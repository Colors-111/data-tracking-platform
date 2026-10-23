<!-- frontend/src/components/FlowsCard.vue — v1.3 总览资金异动卡（自取数，失败只降级本卡不阻断页面） -->
<script setup>
import { ref, computed, onMounted } from "vue";
import { RouterLink } from "vue-router";
import { api } from "../api";
import { pctColor } from "../format.js";
import { fmtSigned, flowRowTarget, mergeTracked } from "../flows.js";

// 已跟踪判定数据：movements 由总览页传入（已加载的 /api/movements，instrument_id=code 带 source_id，
// 覆盖已注册品种）；再叠加自选跟踪源详情（etf-watchlist 的 cards，覆盖一键加入的品种）。
// 自选源未创建/加载失败 → 静默按全未跟踪处理（行不可点），不影响数据展示
const props = defineProps({ movements: { type: Array, default: () => [] } });

const flows = ref(null);
const failed = ref(false);
const watchlist = ref(null);

onMounted(async () => {
  try {
    flows.value = await api.flows(null, 3);
  } catch {
    failed.value = true; // JobStatus 模式：卡片失败只局部提示
  }
  try {
    watchlist.value = await api.sourceDetail("etf-watchlist");
  } catch {
    watchlist.value = null;
  }
});

const trackedMap = computed(() => mergeTracked(props.movements, watchlist.value));
const targetOf = (code) => flowRowTarget(code, trackedMap.value);
</script>

<template>
  <div class="card">
    <h3>💰 全市场资金异动
      <span v-if="flows?.as_of_date" class="asof">{{ flows.as_of_date }}</span>
    </h3>
    <p v-if="failed" class="fail">⚠ 全市场资金流加载失败（<RouterLink to="/flows">/flows</RouterLink> 页可查看）</p>
    <p v-else-if="!flows" class="loading">资金流加载中…</p>
    <template v-else>
      <p class="summary">
        全市场净流入
        <b :class="pctColor(flows.total_change)">{{ fmtSigned(flows.total_change, 1) }}</b> 亿份
        · 覆盖 {{ flows.count }} 只
      </p>
      <div class="cols">
        <div v-for="side in [
              { key: 'in', label: 'TOP3 净申购', rows: flows.top_in },
              { key: 'out', label: 'TOP3 净赎回', rows: flows.top_out }]"
             :key="side.key" class="col">
          <h4 :class="side.key === 'in' ? 'up' : 'down'">{{ side.label }}</h4>
          <!-- 已跟踪品种整行可点进其源页（RouterLink），未跟踪渲染为纯 span（不跳，/flows 页可加入跟踪） -->
          <template v-for="r in side.rows" :key="r.code">
            <component :is="targetOf(r.code) ? RouterLink : 'span'"
                       :to="targetOf(r.code) ?? undefined"
                       class="f-row" :class="{ linked: targetOf(r.code) }">
              <span class="f-name">{{ r.code }} {{ r.name }}</span>
              <span class="num" :class="pctColor(r.change)">{{ fmtSigned(r.change) }}</span>
            </component>
          </template>
          <p v-if="!side.rows?.length" class="none">暂无数据</p>
        </div>
      </div>
      <RouterLink class="more" to="/flows">查看全市场榜单 →</RouterLink>
    </template>
  </div>
</template>

<style scoped>
h3 { margin:0 0 8px; font-size:14px; }
/* 白底卡片上的日期徽章：全局 .badge 是半透明白（配红 banner），此处用浅灰底保证可读 */
.asof { background:#f0f3f6; color:#666; border-radius:10px; padding:2px 10px; font-size:11px; font-weight:400; margin-left:8px; }
.summary { margin:0 0 10px; font-size:13px; color:#555; }
.summary b { font-size:15px; font-variant-numeric: tabular-nums; }
.cols { display:flex; gap:18px; }
.col { flex:1; min-width:0; }
h4 { margin:0 0 4px; font-size:12px; }
.f-row { display:flex; justify-content:space-between; gap:8px; padding:4px 0; font-size:12.5px; border-bottom:1px dashed #f1f1f1; color:inherit; text-decoration:none; }
.f-row.linked:hover { color:#c0392b; }
.f-name { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.num { font-variant-numeric: tabular-nums; flex-shrink:0; }
.none { color:#999; font-size:12px; text-align:center; padding:10px 0; margin:0; }
.more { display:inline-block; margin-top:10px; font-size:12px; color:#c0392b; text-decoration:none; }
.more:hover { text-decoration:underline; }
.loading { color:#999; font-size:12px; margin:4px 0; }
.fail { color:#b03a2e; font-size:12px; margin:4px 0; }
.fail a { color:inherit; }
</style>
