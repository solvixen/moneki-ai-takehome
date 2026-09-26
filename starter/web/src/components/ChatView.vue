<script setup>
import { ref, onMounted } from "vue";
import { postChat } from "../api.js";

const TYPE_LABELS = {
  data: "查数据库",
  doc: "查知识库",
  hybrid: "数据 + 文档",
  refusal: "已拒绝",
  clarify: "需要澄清",
};

const msgs = ref([]); // {role:'user'|'bot', text?, payload?}
const question = ref("");
const busy = ref(false);
const errMsg = ref("");

// 会话 ID 随机生成，保证默认不与历史串线；追问复用同一个
const sessionId = "web-" + Math.random().toString(36).slice(2, 10);

onMounted(() => {
  msgs.value.push({
    role: "bot",
    text:
      "你好，我是经营问答助手。可以问我：\n· 「牛肉poke 六月卖了多少钱？」（查库）\n· 「外卖订单多久内可以退款？」（查文档）\n· 「618 当天 S02 的牛肉poke 达到目标了吗？」（两者都查）",
  });
});

async function ask() {
  const q = question.value.trim();
  if (!q || busy.value) return;
  errMsg.value = "";
  question.value = "";
  msgs.value.push({ role: "user", text: q });
  busy.value = true;
  try {
    const payload = await postChat(sessionId, q);
    msgs.value.push({ role: "bot", payload });
  } catch (err) {
    errMsg.value = err.message;
  } finally {
    busy.value = false;
    scrollBottom();
  }
}

function scrollBottom() {
  requestAnimationFrame(() => {
    const box = document.querySelector(".chat-msgs");
    if (box) box.scrollTop = box.scrollHeight;
  });
}

function pretty(obj) {
  return JSON.stringify(obj, null, 2);
}

const emit = defineEmits(["open-trace"]);
</script>

<template>
  <div class="card">
    <div class="chat-msgs">
      <template v-for="(m, i) in msgs" :key="i">
        <div v-if="m.role === 'user'" class="msg user">{{ m.text }}</div>
        <div v-else-if="!m.payload" class="msg bot">{{ m.text }}</div>
        <div v-else class="msg bot">
          <span class="type-badge" :class="'type-' + m.payload.answer_type">
            {{ TYPE_LABELS[m.payload.answer_type] || m.payload.answer_type }}
          </span>
          <div>{{ m.payload.answer }}</div>

          <div class="cite-block" v-if="m.payload.data_evidence?.length">
            <h4>数据证据（来自数据库真实查询）</h4>
            <div v-for="(ev, j) in m.payload.data_evidence" :key="j">
              <div v-if="ev.tool" class="hint">
                工具：{{ ev.tool }}　参数：{{ JSON.stringify(ev.params) }}
              </div>
              <div v-else-if="ev.sql" class="hint">SQL：{{ ev.sql }}</div>
              <pre class="evidence-pre">{{ pretty(ev.result) }}</pre>
            </div>
          </div>

          <div class="cite-block" v-if="m.payload.citations?.length">
            <h4>文档引用（原文逐字）</h4>
            <div class="cite-item" v-for="(c, j) in m.payload.citations" :key="j">
              <span class="cite-doc">{{ c.doc_id }}</span>{{ c.quote }}
            </div>
          </div>

          <span
            class="trace-link"
            v-if="m.payload.trace_id"
            @click="emit('open-trace', m.payload.trace_id)"
          >查看本次回答的调试追踪 →</span>
        </div>
      </template>
    </div>

    <div class="err-box" v-if="errMsg" style="margin-top: 12px">{{ errMsg }}</div>

    <div class="chat-input">
      <input
        type="text"
        v-model="question"
        placeholder="用自然语言提问，例如：S01 最近一周的净营业额是多少？"
        @keyup.enter="ask"
      />
      <button class="primary" :disabled="busy || !question.trim()" @click="ask">
        {{ busy ? "思考中…" : "发送" }}
      </button>
    </div>
  </div>
</template>
