<script setup>
import { ref, watch, computed, onMounted } from "vue";
import { api } from "../api";
import JobStatus from "../components/JobStatus.vue";
import CardRow from "../components/CardRow.vue";
import TrendChart from "../components/TrendChart.vue";
import CompareChips from "../components/CompareChips.vue";
import { groupSections, groupCharts, isCommodityMeta } from "../metricSections.js";

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
let cseq = 0;
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
    if (my === cseq) {
      compareSeries.value = []; // 失败后诚实回退 own 原值，不让 % 轴静默显示原始值
      compareError.value = e?.message ?? String(e);
    }
  }
}
onMounted(load);
watch([() => props.id, range], load);
// 切源清空对比：对比是"当前源"的上下文，不跨源携带（同时消除自对比 chip）
watch(() => props.id, () => {
  compareIds.value = [];
  normalize.value = 0;
});
watch([compareIds, normalize], loadCompare);

// 对比源的 instrument_meta（iid→unit）：/api/series 不带单位，而商品对比源的
// sources.unit='混合' 非真实量纲——逐对比源拉 sourceDetail 取逐 instrument 单位，
// 供原值模式下对比线按单位落图（归一化合并图不依赖此数据）
const compareMeta = ref({});
let mseq = 0;
watch(compareIds, async (ids) => {
  const my = ++mseq; // 快速增删对比源时丢弃过期响应
  if (!ids.length) {
    compareMeta.value = {};
    return;
  }
  const metas = await Promise.all(
    ids.map(sid => api.sourceDetail(sid).then(d => d.instrument_meta ?? {}).catch(() => ({}))));
  if (my === mseq) compareMeta.value = Object.assign({}, ...metas);
});

const nameOf = (iid) =>
  detail.value?.cards.find(c => c.instrument_id === iid)?.name ?? iid;
// 统一序列模式：/api/series 数据就位时才生效；失败/未返回时回退 own 原值，
// percent/unit 轴也跟着回退，避免 % 轴误标原始值
const useUnified = computed(() =>
  (compareIds.value.length || normalize.value === 1) && compareSeries.value.length > 0);
const chartSeries = computed(() => {
  if (useUnified.value)
    return compareSeries.value.map(s => ({ name: s.name, data: s.points }));
  // 原值模式回退到 detail 自带序列
  return Object.entries(detail.value?.series ?? {})
    .map(([iid, pts]) => ({ name: nameOf(iid), data: pts }))
    .filter(s => s.data.length); // 未回补的空 instrument 不进图例
});

// v1.2 商品组源分区：share-only 源（ETF）走旧单区布局，商品源按指标分区渲染
const isCommodity = computed(() => isCommodityMeta(detail.value?.instrument_meta));
const sections = computed(() => groupSections(detail.value?.instrument_meta ?? {}));
// 对比线落点：只叠加在价格区图表（对比本就为跨源归一化，被比源多为 share/混合单位源、
// 其 metric 不属于本页分区）；无价格区的极端情况回退首个分区
const compareSectionKey = computed(() => {
  const keys = sections.value.map(s => s.key);
  return keys.includes("price") ? "price" : keys[0] ?? null;
});

const cardsOf = (sec) =>
  detail.value.cards.filter(c => sec.iids.includes(c.instrument_id));

// 合并图（归一化模式）序列：统一序列按 instrument_id 归区（own 落各自分区、
// compare 落对比区），归一化后单位消失、区内所有线一张图（Y 轴 %）
function seriesOf(sec) {
  if (useUnified.value) {
    const ownMeta = detail.value.instrument_meta ?? {};
    return compareSeries.value
      .filter(s => sec.iids.includes(s.instrument_id)
                   || (!(s.instrument_id in ownMeta) && sec.key === compareSectionKey.value))
      .map(s => ({ name: s.name, data: s.points }));
  }
  return sec.iids
    .filter(iid => detail.value.series[iid]?.length)
    .map(iid => ({ name: nameOf(iid), data: detail.value.series[iid] }));
}

// 线的计价单位：own 线用本源 instrument_meta，对比线用对比源 instrument_meta；
// 都取不到（对比源元数据在途/加载失败）归入"其他"组，不静默丢线
function unitOfSeries(s) {
  return (detail.value?.instrument_meta ?? {})[s.instrument_id]?.unit
      ?? compareMeta.value[s.instrument_id]?.unit ?? "其他";
}

// 原值模式的图组：自有单位组（groupCharts，组序=区内单位首现序）+ 对比线单位
// 并入同名组或追加新组（对比线只落对比区）
function chartGroupsOf(sec) {
  const ownMeta = detail.value.instrument_meta ?? {};
  const groups = groupCharts(sec.iids, ownMeta);
  if (!useUnified.value || sec.key !== compareSectionKey.value) return groups;
  for (const s of compareSeries.value) {
    if (s.instrument_id in ownMeta) continue;
    const u = unitOfSeries(s);
    if (!groups.some(g => g.unit === u)) groups.push({ unit: u, iids: [] });
  }
  return groups;
}

// 原值模式某图组的序列：有对比时（统一序列）own+compare 都取 compareSeries 原值
// 按单位落组；无对比时回退 detail.series（空序列不进图例）
function seriesOfGroup(sec, g) {
  if (useUnified.value) {
    const ownMeta = detail.value.instrument_meta ?? {};
    return compareSeries.value
      .filter(s => unitOfSeries(s) === g.unit
                   && (ownMeta[s.instrument_id]
                       ? sec.iids.includes(s.instrument_id)
                       : sec.key === compareSectionKey.value))
      .map(s => ({ name: s.name, data: s.points }));
  }
  return g.iids
    .filter(iid => detail.value.series[iid]?.length)
    .map(iid => ({ name: nameOf(iid), data: detail.value.series[iid] }));
}

// 每分区渲染的图列表：
//  - 归一化（统一序列生效）：区内合并一张图——归一化后单位消失可比，Y 轴 %；
//  - 原值：按计价单位分图（图标题带单位，如"价格（元/克）"），
//    对比线按各自单位落入对应子图
const sectionCharts = computed(() => sections.value.map(sec => {
  const charts = [];
  if (useUnified.value && normalize.value === 1) {
    const series = seriesOf(sec);
    if (series.length)
      charts.push({ key: "merged", title: "", unit: null, percent: true, series });
  } else {
    for (const g of chartGroupsOf(sec)) {
      const series = seriesOfGroup(sec, g);
      if (series.length)
        charts.push({ key: g.unit, title: `${sec.label}（${g.unit}）`,
                      unit: g.unit, percent: false, series });
    }
  }
  return { ...sec, charts };
}));

// 量纲约束（Task 14 审查）：compare 引入不同 unit 的源时自动置归一化（用户可手动关回）
function onAdded(payload) {
  if (payload?.unit && detail.value?.unit && payload.unit !== detail.value.unit) {
    normalize.value = 1;
  }
  // 商品源 sources.unit='混合' 已非真实量纲，跨源对比必跨指标单位——一律归一化
  if (isCommodity.value) {
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

    <!-- ETF 等全 share 源：单区旧布局（Task 14/17 契约不动） -->
    <template v-if="!isCommodity">
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
        <TrendChart :series="chartSeries" :percent="useUnified && normalize === 1"
                    :unit="useUnified && normalize === 1 ? null : detail.unit"
                    :update-options="{ notMerge: true }" />
      </div>
      <div class="card empty" v-else>数据累积中，暂无历史序列</div>
    </template>

    <!-- v1.2 商品组源：按指标分区；全页一个归一化开关作用于所有分区。
         归一化：区内合并一张图（% 轴）；原值：按计价单位分图，对比线按各自单位落子图 -->
    <template v-else>
      <div class="compare-bar">
        <CompareChips v-model="compareIds" :exclude-ids="[id]" @added="onAdded" />
        <label class="norm-toggle">
          <input type="checkbox" :checked="normalize === 1" @change="normalize = $event.target.checked ? 1 : 0">
          归一化（期初=100）
        </label>
        <span v-if="compareError" class="compare-error">对比序列加载失败：{{ compareError }}</span>
      </div>

      <section v-for="sec in sectionCharts" :key="sec.key" class="metric-section">
        <h3 class="section-title">{{ sec.label }}<span v-if="sec.note" class="section-note">{{ sec.note }}</span></h3>
        <CardRow v-if="cardsOf(sec).length" :cards="cardsOf(sec)" :unit="detail.unit" use-card-unit />
        <template v-if="sec.charts.length">
          <div class="card" v-for="ch in sec.charts" :key="`${sec.key}:${ch.key}`">
            <h4 v-if="ch.title" class="chart-title">{{ ch.title }}</h4>
            <TrendChart :series="ch.series" :percent="ch.percent" :unit="ch.unit"
                        :update-options="{ notMerge: true }" />
          </div>
        </template>
        <div class="card empty" v-else>数据累积中</div>
      </section>
    </template>
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
.metric-section { margin-top:6px; }
.section-title { font-size:14px; color:#444; margin:12px 0 0; padding-left:8px; border-left:3px solid #c0392b; }
.section-note { margin-left:8px; font-size:11px; color:#999; font-weight:400; }
.chart-title { font-size:13px; color:#666; margin:0 0 2px; font-weight:600; }
</style>
