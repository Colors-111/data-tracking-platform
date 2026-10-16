<!-- frontend/src/components/MovementsTable.vue -->
<script setup>
import { fmtVal, fmtChange, pctColor, sparkBars } from "../format.js";
defineProps({ movements: Array, asOf: String });
</script>

<template>
  <table class="mv-table">
    <thead><tr><th>数据源 / 品种</th><th class="num">最新值</th><th class="num">变化</th><th class="num">5日趋势</th></tr></thead>
    <tbody>
      <tr v-for="m in movements" :key="m.instrument_id"
          :title="m.trade_date !== asOf ? `数据日期 ${m.trade_date}（源更新滞后）` : ''">
        <td><RouterLink :to="`/source/${m.source_id}`" class="mv-link">
          {{ m.source_name }} · {{ m.instrument_name }}</RouterLink>
          <span v-if="m.trade_date !== asOf" class="stale-tag">{{ m.trade_date }}</span></td>
        <td class="num">{{ fmtVal(m.value, m.unit) }}</td>
        <td class="num" :class="pctColor(m.pct)">{{ fmtChange(m.change, m.pct) }}</td>
        <td class="num spark" :class="pctColor(m.pct)">{{ sparkBars(m.spark5) }}</td>
      </tr>
    </tbody>
  </table>
  <p v-if="!movements.length" class="none">今日无异动（未超各源阈值）</p>
</template>

<style scoped>
.mv-table { width:100%; border-collapse:collapse; font-size:13px; }
.mv-table th { color:#999; font-weight:400; text-align:left; padding:6px 8px; border-bottom:1px solid #eee; }
.mv-table td { padding:8px; border-bottom:1px solid #f3f3f3; }
.num { text-align:right; font-variant-numeric: tabular-nums; }
.mv-link { color:inherit; text-decoration:none; }
.mv-link:hover { color:#c0392b; }
.spark { letter-spacing: 2px; }
.stale-tag { margin-left:6px; font-size:10px; color:#e67e22; background:#fef5e7; border-radius:3px; padding:1px 5px; }
.none { color:#999; text-align:center; padding:24px; }
</style>
