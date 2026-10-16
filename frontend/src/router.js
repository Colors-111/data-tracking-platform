import { createRouter, createWebHistory } from "vue-router";
import OverviewView from "./views/OverviewView.vue";
import SourceDetailView from "./views/SourceDetailView.vue";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", component: OverviewView },
    { path: "/source/:id", component: SourceDetailView, props: true },
  ],
});
