<script setup>
import { ref, watch, computed, onMounted } from "vue";
import { api } from "../api";
import JobStatus from "../components/JobStatus.vue";
import CardRow from "../components/CardRow.vue";
import TrendChart from "../components/TrendChart.vue";

const props = defineProps({ id: String });
const RANGES = [["1m", "1月"], ["3m", "3月"], ["6m", "6月"], ["1y", "1年"], ["all", "全部"]];
const detail = ref(null);
const error = ref("");
const range = ref("3m");

let seq = 0;
async function load() {
  const my = ++seq; // 快速切换 range/id 时丢弃过期响应
  error.value = "";
  try {
    const d = await api.sourceDetail(props.id, range.value);
    if (my === seq) detail.value = d;
  } catch (e) {
    if (my === seq) {
      detail.value = null;
      error.value = e?.message ?? String(e);
    }
  }
}
onMounted(load);
watch([() => props.id, range], load);

const nameOf = (iid) =>
  detail.value?.cards.find(c => c.instrument_id === iid)?.name ?? iid;
const chartSeries = computed(() =>
  Object.entries(detail.value?.series ?? {})
    .map(([iid, pts]) => ({ name: nameOf(iid), data: pts }))
    .filter(s => s.data.length)); // 未回补的空 instrument 不进图例
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

    <div class="card" v-if="detail.as_of_date">
      <TrendChart :series="chartSeries" :unit="detail.unit" />
    </div>
    <div class="card empty" v-else>数据累积中，暂无历史序列</div>
  </div>
</template>

<style scoped>
.range-bar { margin: 12px 0 4px; display:flex; gap:6px; }
.range-bar button { border:1px solid #ddd; background:#fff; border-radius:4px; padding:4px 12px; cursor:pointer; font-size:12px; }
.range-bar button.active { background:#f0f3f6; color:#c0392b; font-weight:700; border-color:#c0392b; }
.empty { color:#999; text-align:center; padding:40px; }
</style>
