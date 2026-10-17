import { createRouter, createWebHistory } from "vue-router";
import OverviewView from "./views/OverviewView.vue";
import SourceDetailView from "./views/SourceDetailView.vue";
import FlowsView from "./views/FlowsView.vue";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", component: OverviewView },
    { path: "/source/:id", component: SourceDetailView, props: true },
    { path: "/flows", component: FlowsView },
  ],
});
