<script setup>
import { computed } from "vue";
import { use } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { LineChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent, DataZoomComponent } from "echarts/components";
import VChart from "vue-echarts";
use([CanvasRenderer, LineChart, GridComponent, LegendComponent, TooltipComponent, DataZoomComponent]);

const props = defineProps({
  series: { type: Array, default: () => [] }, // [{name, data: [[date, v],...]}]
  unit: String,
  percent: Boolean, // 归一化模式时 Y 轴单位 %
});
const PALETTE = ["#e74c3c", "#3498db", "#f39c12", "#27ae60", "#9b59b6", "#16a085", "#8e44ad", "#d35400"];

const option = computed(() => ({
  color: PALETTE,
  tooltip: { trigger: "axis" },
  legend: { type: "scroll", top: 0 },
  grid: { left: 60, right: 20, top: 36, bottom: 56 },
  xAxis: { type: "time" },
  yAxis: { type: "value", scale: true,
           name: props.percent ? "%" : (props.unit ?? "") },
  dataZoom: [{ type: "inside" }, { type: "slider", height: 18, bottom: 10 }],
  series: props.series.map(s => ({
    name: s.name, type: "line", showSymbol: false,
    connectNulls: true, data: s.data,
  })),
}));
</script>

<template>
  <VChart :option="option" autoresize style="height: 420px" />
</template>
