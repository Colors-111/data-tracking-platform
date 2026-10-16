<!-- frontend/src/components/SourceTiles.vue -->
<script setup>
defineProps({ groups: Array });   // 来自 /api/sources
</script>

<template>
  <div v-for="g in groups" :key="g.group" class="tile-group">
    <div class="tile-group-title" v-if="g.group">{{ g.group }}</div>
    <div class="tiles">
      <RouterLink v-for="s in g.sources" :key="s.id" class="tile"
                  :to="`/source/${s.id}`">
        <div class="tile-name">{{ s.name }}</div>
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
.tile-meta { color:#999; font-size:11px; margin-top:4px; }
</style>
