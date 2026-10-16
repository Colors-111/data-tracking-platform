<script setup>
import { fmtVal, fmtChange, pctColor } from "../format.js";
defineProps({ cards: Array, unit: String });
</script>

<template>
  <div class="cards-row">
    <div v-for="c in cards" :key="c.instrument_id" class="inst-card">
      <div class="inst-name">{{ c.name }}<span v-if="c.code"> · {{ c.code }}</span></div>
      <div class="inst-value">{{ fmtVal(c.value, unit) }}</div>
      <div :class="pctColor(c.day_change ?? 0)" v-if="c.day_change !== null">
        {{ fmtChange(c.day_change, c.day_pct) }}
      </div>
      <div class="range-change" v-if="c.range_pct !== null" :class="pctColor(c.range_change ?? 0)">
        区间 {{ fmtChange(c.range_change, c.range_pct) }}
      </div>
    </div>
  </div>
</template>

<style scoped>
.cards-row { display: flex; gap: 10px; flex-wrap: wrap; margin: 14px 0; }
.inst-card { background:#fff; border-radius:8px; padding:12px 16px; min-width:170px; flex:1; cursor:default; }
.inst-name { color:#666; font-size:12px; }
.inst-value { font-size:20px; font-weight:700; margin:4px 0; }
.range-change { color:#999; font-size:11px; margin-top:2px; }
</style>
