<script setup>
import { ref, onMounted } from "vue";
import { getHealth } from "./api.js";
import DashboardView from "./components/DashboardView.vue";
import ChatView from "./components/ChatView.vue";
import TraceView from "./components/TraceView.vue";

const tab = ref("dashboard");
const health = ref(null);
const lastTraceId = ref("");

onMounted(async () => {
  try {
    health.value = await getHealth();
  } catch (err) {
    // 服务未启动时看板内会再提示，头部保持安静
  }
});

function switchTab(name) {
  tab.value = name;
}

function openTrace(traceId) {
  lastTraceId.value = traceId;
  tab.value = "trace";
}
defineExpose({ switchTab });
</script>

<template>
  <header class="app-header">
    <h1>连锁餐饮经营看板</h1>
    <div class="health-badge" v-if="health">
      <span>今天：{{ health.today }}</span>
      <span>数据区间：{{ health.data_period?.start }} ~ {{ health.data_period?.end }}</span>
      <span>知识库：{{ health.kb_docs }} 篇 / {{ health.kb_chunks }} 块</span>
      <span class="mode-pill" :class="health.llm_mode === 'live' ? 'live' : 'mock'">
        {{ health.llm_mode === "live" ? "live 模型" : "mock 降级" }}
      </span>
    </div>
  </header>

  <nav class="tabs">
    <button class="tab-btn" :class="{ active: tab === 'dashboard' }" @click="switchTab('dashboard')">
      经营看板
    </button>
    <button class="tab-btn" :class="{ active: tab === 'chat' }" @click="switchTab('chat')">
      AI 问答
    </button>
    <button class="tab-btn" :class="{ active: tab === 'trace' }" @click="switchTab('trace')">
      调试面板
    </button>
  </nav>

  <DashboardView v-show="tab === 'dashboard'" />
  <ChatView v-show="tab === 'chat'" @open-trace="openTrace" />
  <TraceView v-show="tab === 'trace'" :trace-id="lastTraceId" />
</template>
