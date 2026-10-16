<script setup>
import { ref, onMounted } from "vue";
import { api } from "./api";
// 约定：视图内各自 catch fetch 错误 → 主区用 .banner 展示；侧边栏失败只影响导航不影响页面
const groups = ref([]);
const loadError = ref(false);
onMounted(async () => {
  try {
    groups.value = await api.sources();
    loadError.value = false;
  } catch {
    loadError.value = true;
  }
});
</script>

<template>
  <div class="layout">
    <aside class="sidebar">
      <div class="brand">数据追踪<span>ETF · 大宗商品</span></div>
      <nav>
        <RouterLink class="nav-item overview" to="/">📊 总览</RouterLink>
        <div v-for="g in groups" :key="g.group">
          <div class="group-label">{{ g.group }}</div>
          <RouterLink v-for="s in g.sources" :key="s.id" class="nav-item" :to="`/source/${s.id}`">
            <span class="dot" />{{ s.name }}
          </RouterLink>
        </div>
        <div v-if="loadError" class="side-error">⚠ 数据源加载失败</div>
      </nav>
    </aside>
    <main class="main"><RouterView /></main>
  </div>
</template>

<style>
:root { --up:#e74c3c; --down:#27ae60; --flat:#95a5a6; --sidebar:#1e2430; --sidebar-active:#c0392b; }
* { box-sizing: border-box; }
body { margin: 0; font-family: system-ui, "PingFang SC", "Microsoft YaHei", sans-serif; background:#f5f6f8; }
.layout { display: flex; min-height: 100vh; }
.sidebar { width: 210px; background: var(--sidebar); color: #aab; padding: 16px 10px; flex-shrink: 0; }
.brand { color: #fff; font-weight: 700; font-size: 18px; margin-bottom: 4px; }
.brand span { display:block; color:#667; font-size: 11px; font-weight: 400; }
.group-label { color:#556; font-size: 11px; margin: 14px 8px 4px; }
.nav-item { display:block; color:#aab; text-decoration:none; padding:7px 10px; border-radius:5px; font-size:13px; }
.nav-item.router-link-active { background: var(--sidebar-active); color:#fff; }
.nav-item.overview { margin-bottom: 6px; }
.dot { display:inline-block; width:7px; height:7px; border-radius:50%; background:#3a4a5f; margin-right:7px; }
.main { flex:1; padding: 18px; }
.up { color: var(--up); } .down { color: var(--down); } .flat { color: var(--flat); }
.card { background:#fff; border-radius:8px; padding:14px 16px; }
.banner { background: var(--sidebar-active); color:#fff; padding:12px 18px; border-radius:8px; font-weight:700; }
.side-error { color:#7a8290; font-size:11px; padding:7px 10px; }
.badge { background:rgba(255,255,255,.25); border-radius:10px; padding:2px 10px; font-size:12px; font-weight:400; margin-left:8px; }
</style>
