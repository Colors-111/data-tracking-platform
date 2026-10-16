<script setup>
import { ref, onMounted } from "vue";
import { api } from "../api";
const jobs = ref([]);
const failed = ref(false);
onMounted(async () => {
  // 辅助小部件：失败只静默降级，不影响所在视图的错误态
  try {
    (await api.jobs()).jobs.forEach(j => jobs.value.push(j));
  } catch {
    failed.value = true;
  }
});
</script>

<template>
  <span class="job-status">
    <template v-if="failed">状态加载失败</template>
    <template v-else>
      <template v-for="(j, i) in jobs" :key="i">
        <span :class="j.status === 'ok' ? 'flat' : 'down'">
          {{ j.source_id }}:{{ j.status }}</span><span v-if="i < jobs.length-1"> · </span>
      </template>
    </template>
  </span>
</template>

<style scoped>
.job-status { font-size: 11px; color: #999; }
/* 红底 .banner 内的对比度处理：
   - .down 绿色 #27ae60 在红底上几乎不可读 → 浅珊瑚 #ffd2cc + 加粗，保留"非 ok 需注意"的语义
   - .flat 灰色 #95a5a6 在红底上发闷 → 半透明白
   选择器限定在 .banner 祖先下，白底复用（如总览页）时仍回落到全局 .flat/.down 颜色 */
.banner .job-status .down { color: #ffd2cc; font-weight: 600; }
.banner .job-status .flat { color: rgba(255, 255, 255, .78); }
</style>
