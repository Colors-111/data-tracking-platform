<!-- frontend/src/views/OverviewView.vue -->
<script setup>
import { ref, onMounted } from "vue";
import { api } from "../api";
import MovementsTable from "../components/MovementsTable.vue";
import SourceTiles from "../components/SourceTiles.vue";
import JobStatus from "../components/JobStatus.vue";
import FlowsCard from "../components/FlowsCard.vue";

const movements = ref([]);
const groups = ref([]);   // 保留 /api/sources 的分组结构 [{group, sources}]，直接交给 SourceTiles
const asOf = ref(null);
const error = ref(null);
const loaded = ref(false);   // 请求完成前不渲染列表区，防闪现"今日无异动"假空态

onMounted(async () => {
  try {
    const [mv, src] = await Promise.all([api.movements(), api.sources()]);
    movements.value = mv.movements;
    asOf.value = mv.as_of_date;
    groups.value = src;
    loaded.value = true;
  } catch (e) {
    error.value = String(e.message ?? e);   // Task 13 约定：view 内 catch → 主区 .banner
  }
});
</script>

<template>
  <div class="banner error" v-if="error">加载失败：{{ error }}</div>
  <div v-else-if="!loaded" class="card">总览加载中…</div>
  <template v-else>
    <div class="banner">总览<span class="badge">数据截至 {{ asOf ?? "—" }}</span>
      <JobStatus />
    </div>
    <!-- v1.3 资金异动卡：自取数失败只降级本卡；movements 供已跟踪（code→源）判定 -->
    <FlowsCard class="sec" :movements="movements" />
    <div class="card sec">
      <h3>⚡ 今日异动 <small>跨全部数据源 · 按变化幅度排序</small></h3>
      <MovementsTable :movements="movements" :as-of="asOf" />
    </div>
    <div class="card sec">
      <h3>分组速览</h3>
      <SourceTiles :groups="groups" />
    </div>
  </template>
</template>

<style scoped>
.sec { margin-top: 14px; }
h3 small { color:#999; font-size:11px; font-weight:400; margin-left:8px; }
.banner.error { background:#7f8c8d; }   /* 错误态 banner 用中性色，别和正常红色 banner 混淆 */
</style>
