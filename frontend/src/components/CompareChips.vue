<script setup>
import { ref, computed, onMounted } from "vue";
import { api } from "../api";

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  excludeIds: { type: Array, default: () => [] },
});
const emit = defineEmits(["update:modelValue", "added"]);
const picking = ref(false);
const allSources = ref([]);
const pickerError = ref("");

async function loadSources() {
  if (allSources.value.length) return;
  try {
    allSources.value = (await api.sources()).flatMap(g => g.sources);
    pickerError.value = "";
  } catch (e) {
    pickerError.value = e?.message ?? String(e);
  }
}

async function openPicker() {
  picking.value = !picking.value;
  if (picking.value) await loadSources();
}

// 候选粒度：数据源级（后端 /api/series 会把 source id 展开为其全部成分序列）；
// 排除自身（excludeIds）与已添加的源
const candidates = computed(() =>
  allSources.value.filter(s =>
    !props.excludeIds.includes(s.id) && !props.modelValue.includes(s.id)));

const add = (sid) => {
  if (!props.modelValue.includes(sid)) {
    emit("update:modelValue", [...props.modelValue, sid]);
    // 量纲约束：带上 unit 供父组件在跨单位时自动置归一化
    const src = allSources.value.find(s => s.id === sid);
    emit("added", { sid, unit: src?.unit ?? null });
  }
  picking.value = false;
};
const remove = (sid) =>
  emit("update:modelValue", props.modelValue.filter(x => x !== sid));

// chip 显示 name（value 仍是 sid）；allSources 未加载时回退显示 sid
const nameOf = (sid) => allSources.value.find(s => s.id === sid)?.name ?? sid;
// 父组件预置 modelValue（非经 picker 添加）时也拉一次 sources 以补 name
onMounted(() => { if (props.modelValue.length) loadSources(); });
</script>

<template>
  <div class="chips">
    <span v-for="sid in modelValue" :key="sid" class="chip added" :title="`${nameOf(sid)}（点击移除）`" @click="remove(sid)">
      {{ nameOf(sid) }} ✕
    </span>
    <span class="chip add-btn" @click="openPicker">+ 添加对比 ▾</span>
    <div v-if="picking" class="picker">
      <div v-if="pickerError" class="picker-empty">数据源列表加载失败：{{ pickerError }}</div>
      <template v-else-if="candidates.length">
        <div v-for="c in candidates" :key="c.id" class="picker-item" @click="add(c.id)">
          {{ c.name }} <span class="picker-unit">{{ c.unit }}</span>
        </div>
      </template>
      <div v-else class="picker-empty">没有可添加的数据源</div>
    </div>
  </div>
</template>

<style scoped>
.chips { display:flex; gap:6px; flex-wrap:wrap; position:relative; align-items:center; }
.chip { border:1px dashed #999; border-radius:12px; padding:2px 10px; font-size:12px; cursor:pointer; }
.chip.added { border-style:solid; background:#eaf4fd; color:#2980b9; border-color:#3498db; }
.add-btn:hover { border-color:#c0392b; color:#c0392b; }
.picker { position:absolute; top:28px; left:0; background:#fff; border:1px solid #ddd; border-radius:6px; z-index:10; min-width:180px; max-height:260px; overflow:auto; box-shadow:0 4px 12px rgba(0,0,0,.08); }
.picker-item { padding:8px 12px; cursor:pointer; font-size:13px; }
.picker-item:hover { background:#f5f6f8; }
.picker-unit { color:#999; font-size:11px; margin-left:4px; }
.picker-empty { padding:10px 12px; color:#999; font-size:12px; }
</style>
