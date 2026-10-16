<script setup>
import { ref, watch, computed, onMounted } from "vue";
import { api } from "../api";
import JobStatus from "../components/JobStatus.vue";
import CardRow from "../components/CardRow.vue";
import TrendChart from "../components/TrendChart.vue";
import CompareChips from "../components/CompareChips.vue";

const props = defineProps({ id: String });
const RANGES = [["1m", "1月"], ["3m", "3月"], ["6m", "6月"], ["1y", "1年"], ["all", "全部"]];
const detail = ref(null);
const error = ref("");
const range = ref("3m");
const compareIds = ref([]);      // 添加对比的数据源 id 列表
const compareSeries = ref([]);   // /api/series 返回（含 own + compare）
const compareError = ref("");    // 对比序列加载失败：局部提示，不覆盖卡片数据
const normalize = ref(0);

let seq = 0;
async function load() {
  const my = ++seq; // 快速切换 range/id 时丢弃过期响应
  cseq++; // 作废在途的对比序列响应（旧源/旧 range 的 own ids 不再适用）
  error.value = "";
  detail.value = null; // 切换时立即清空，防旧源数据闪现（Task 14 审查 backlog）
  compareSeries.value = [];
  try {
    const d = await api.sourceDetail(props.id, range.value);
    if (my === seq) {
      detail.value = d;
      await loadCompare();
    }
  } catch (e) {
    if (my === seq) error.value = e?.message ?? String(e);
  }
}

// own + compare 统一走 /api/series（归一化与原值都由后端算好）
let cseq = 0;
async function loadCompare() {
  const my = ++cseq; // 快速切换 compareIds/normalize 时丢弃过期响应
  compareError.value = "";
  const ownIds = Object.keys(detail.value?.series ?? {});
  if (!compareIds.value.length && normalize.value !== 1) {
    compareSeries.value = [];
    return;
  }
  try {
    const allIds = [...ownIds, ...compareIds.value];
    const res = await api.series(allIds, { normalize: normalize.value, range: range.value });
    if (my === cseq) compareSeries.value = res.series;
  } catch (e) {
    if (my === cseq) compareError.value = e?.message ?? String(e);
  }
}
onMounted(load);
watch([() => props.id, range], load);
watch([compareIds, normalize], loadCompare);

const nameOf = (iid) =>
  detail.value?.cards.find(c => c.instrument_id === iid)?.name ?? iid;
const chartSeries = computed(() => {
  // 对比/归一化模式：用 /api/series 返回的统一序列（后端已算好原值或期初=100）
  if ((compareIds.value.length || normalize.value === 1) && compareSeries.value.length)
    return compareSeries.value.map(s => ({ name: s.name, data: s.points }));
  // 纯原值模式（或归一化序列尚未返回/为空）回退到 detail 自带序列
  return Object.entries(detail.value?.series ?? {})
    .map(([iid, pts]) => ({ name: nameOf(iid), data: pts }))
    .filter(s => s.data.length); // 未回补的空 instrument 不进图例
});

// 量纲约束（Task 14 审查）：compare 引入不同 unit 的源时自动置归一化（用户可手动关回）
function onAdded(payload) {
  if (payload?.unit && detail.value?.unit && payload.unit !== detail.value.unit) {
    normalize.value = 1;
  }
}
</script>

<template>
  <div v-if="error" class="banner">加载失败：{{ error }}</div>
  <div v-else-if="!detail" class="card">详情加载中…</div>
  <div v-else>
    <div class="banner">
      {{ detail.name }}
      <span class="badge">{{ detail.cards.length }} 项 · 数据截至 {{ detail.as_of_date ?? "累积中" }}</span>
      <JobStatus />
    </div>

    <div class="range-bar">
      <button v-for="[k, label] in RANGES" :key="k" :class="{ active: range === k }" @click="range = k">{{ label }}</button>
    </div>

    <CardRow :cards="detail.cards" :unit="detail.unit" />

    <div class="compare-bar">
      <CompareChips v-model="compareIds" :exclude-ids="[id]" @added="onAdded" />
      <label class="norm-toggle">
        <input type="checkbox" :checked="normalize === 1" @change="normalize = $event.target.checked ? 1 : 0">
        归一化（期初=100）
      </label>
      <span v-if="compareError" class="compare-error">对比序列加载失败：{{ compareError }}</span>
    </div>

    <div class="card" v-if="detail.as_of_date || (compareIds.length && chartSeries.length)">
      <TrendChart :series="chartSeries" :percent="normalize === 1"
                  :unit="normalize === 1 ? null : detail.unit"
                  :update-options="{ notMerge: true }" />
    </div>
    <div class="card empty" v-else>数据累积中，暂无历史序列</div>
  </div>
</template>

<style scoped>
.range-bar { margin: 12px 0 4px; display:flex; gap:6px; }
.range-bar button { border:1px solid #ddd; background:#fff; border-radius:4px; padding:4px 12px; cursor:pointer; font-size:12px; }
.range-bar button.active { background:#f0f3f6; color:#c0392b; font-weight:700; border-color:#c0392b; }
.compare-bar { display:flex; gap:14px; align-items:center; margin:10px 0; }
.norm-toggle { font-size:12px; color:#666; }
.compare-error { font-size:12px; color:#c0392b; }
.empty { color:#999; text-align:center; padding:40px; }
</style>
