<!-- frontend/src/components/SourceTiles.vue -->
<script setup>
import { fmtVal, fmtChange, pctColor } from "../format.js";
defineProps({ groups: Array });   // 来自 /api/sources

// 今日变化 pct 前端算：基为 total_value - day_change（即昨日合计）
const dayPct = (s) => {
  const base = s.total_value - s.day_change;
  return base ? (s.day_change / base) * 100 : 0;
};
// 防误导：day_change 缺失或基为 0（除零）时只显示值不显示变化
const hasChange = (s) => s.day_change != null && (s.total_value - s.day_change) !== 0;
</script>

<template>
  <div v-for="g in groups" :key="g.group" class="tile-group">
    <div class="tile-group-title" v-if="g.group">{{ g.group }}</div>
    <div class="tiles">
      <RouterLink v-for="s in g.sources" :key="s.id" class="tile"
                  :to="`/source/${s.id}`">
        <div class="tile-name">{{ s.name }}</div>
        <div class="tile-val">
          <template v-if="s.total_value != null">
            {{ fmtVal(s.total_value, s.unit) }}
            <span v-if="hasChange(s)" :class="pctColor(s.day_change)">{{ fmtChange(s.day_change, dayPct(s)) }}</span>
          </template>
          <!-- v1.2：unit='混合' 的商品组源不跨量纲求和（API 置 NULL）——有数据显示"数据见详情"，无数据显示"—" -->
          <span v-else-if="s.latest_date" class="tile-nosum">数据见详情</span>
          <span v-else>—</span>
        </div>
        <div class="tile-meta">{{ s.inst_count }} 项 · 数据截至 {{ s.latest_date ?? "累积中" }}</div>
      </RouterLink>
    </div>
  </div>
</template>

<style scoped>
.tile-group { margin-bottom: 12px; }
.tile-group-title { font-size:12px; color:#888; margin-bottom:6px; }
.tiles { display:flex; gap:10px; flex-wrap:wrap; }
.tile { background:#fff; border-radius:8px; padding:12px 16px; min-width:180px; text-decoration:none; color:inherit; border-left:3px solid #c0392b; }
.tile-name { font-weight:600; font-size:14px; }
.tile-val { font-size:16px; font-weight:700; margin-top:4px; font-variant-numeric: tabular-nums; }
.tile-val span { font-size:11px; font-weight:400; margin-left:4px; }
.tile-nosum { color:#999; font-size:12px; font-weight:400; }
.tile-meta { color:#999; font-size:11px; margin-top:4px; }
</style>
