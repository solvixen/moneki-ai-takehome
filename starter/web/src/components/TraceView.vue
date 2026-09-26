<script setup>
import { ref, watch } from "vue";
import { getTrace } from "../api.js";

const props = defineProps({ traceId: { type: String, default: "" } });

const inputId = ref("");
const trace = ref(null);
const errMsg = ref("");
const loading = ref(false);

watch(
  () => props.traceId,
  (v) => {
    if (v) {
      inputId.value = v;
      load();
    }
  }
);

async function load() {
  const id = (inputId.value || "").trim();
  if (!id || loading.value) return;
  loading.value = true;
  errMsg.value = "";
  trace.value = null;
  try {
    trace.value = await getTrace(id);
  } catch (err) {
    errMsg.value = err.message;
  } finally {
    loading.value = false;
  }
}

function pretty(obj) {
  if (obj === null || obj === undefined) return "";
  if (typeof obj === "string") return obj;
  return JSON.stringify(obj, null, 2);
}

function ms(step) {
  const v = step.took_ms ?? step.elapsed_ms ?? step.duration_ms;
  return v === undefined ? "" : v + " ms";
}
</script>

<template>
  <div>
    <div class="card" style="display: flex; gap: 12px; align-items: center">
      <input
        type="text"
        v-model="inputId"
        placeholder="输入 trace_id（从问答页的“调试追踪”链接自动带入）"
        style="flex: 1"
        @keyup.enter="load"
      />
      <button class="primary" :disabled="loading || !inputId.trim()" @click="load">
        {{ loading ? "加载中…" : "查看追踪" }}
      </button>
    </div>

    <div class="err-box" v-if="errMsg">{{ errMsg }}</div>

    <template v-if="trace">
      <div class="card" v-if="trace.errors?.length">
        <h3 style="color: var(--red)">错误（{{ trace.errors.length }}）</h3>
        <pre class="evidence-pre" style="background: #fdecec; border-color: #f5c6c6">{{
          pretty(trace.errors)
        }}</pre>
      </div>

      <div class="card">
        <h3>处理步骤（{{ trace.steps?.length || 0 }} 步）</h3>
        <div class="trace-step" v-for="(step, i) in trace.steps || []" :key="i">
          <div class="head">
            <span class="name">{{ i + 1 }}. {{ step.name || step.step || "step" }}</span>
            <span class="ms" v-if="ms(step)">{{ ms(step) }}</span>
          </div>
          <details v-if="step.detail !== undefined && step.detail !== null">
            <summary>查看明细</summary>
            <pre class="evidence-pre">{{ pretty(step.detail) }}</pre>
          </details>
        </div>
      </div>

      <div class="card" v-if="trace.llm_calls?.length">
        <h3>大模型调用（{{ trace.llm_calls.length }} 次）</h3>
        <div class="trace-step" v-for="(call, i) in trace.llm_calls" :key="i">
          <div class="head">
            <span class="name">调用 {{ i + 1 }}</span>
            <span class="ms" v-if="call.elapsed_ms">{{ call.elapsed_ms }} ms</span>
          </div>
          <details open>
            <summary>请求 / 响应</summary>
            <pre class="evidence-pre">{{ pretty(call) }}</pre>
          </details>
        </div>
      </div>
    </template>
  </div>
</template>
