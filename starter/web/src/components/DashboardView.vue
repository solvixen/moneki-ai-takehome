<script setup>
import { ref, onMounted, watch } from "vue";
import * as echarts from "echarts";
import { getHealth, getStores, getSummary, getDaily, getTopProducts, getDataQuality } from "../api.js";

// ---- 筛选条件 ----
const stores = ref([]);
const storeId = ref("");
const startDate = ref("");
const endDate = ref("");
const loading = ref(false);
const errMsg = ref("");

// ---- 数据 ----
const summary = ref(null);
const daily = ref([]);
const top = ref([]);

// ---- 数据质量 ----
const quality = ref(null);

const REMOVED_LABELS = {
  "1_unparseable_date": "日期无法解析",
  "2_empty_amount": "金额为空",
  "3_qty_le_zero": "数量 ≤ 0",
  "4_store_not_in_stores": "门店外键无效",
  "5_product_not_in_products": "商品外键无效",
  "6_duplicate_row": "完全重复行",
  note_unparseable_amount: "金额无法解析",
};

const KPIS = [
  { key: "net_revenue", label: "净营业额", money: true },
  { key: "refund_amount", label: "退款金额", money: true },
  { key: "orders", label: "有效订单数" },
  { key: "aov", label: "客单价", money: true },
  { key: "qty", label: "销量" },
];

const chartEl = ref(null);
let chart = null;

onMounted(async () => {
  chart = echarts.init(chartEl.value);
  try {
    const [health, storeList] = await Promise.all([getHealth(), getStores()]);
    stores.value = storeList.stores || [];
    if (health?.data_period?.start) {
      // 默认区间：数据区间的最后 30 天
      endDate.value = health.data_period.end;
      const end = new Date(health.data_period.end + "T00:00:00");
      const start = new Date(end.getTime() - 29 * 86400000);
      startDate.value = start.toISOString().slice(0, 10);
    }
    quality.value = await getDataQuality();
    await refresh();
  } catch (err) {
    errMsg.value = err.message;
  }
  window.addEventListener("resize", () => chart && chart.resize());
});

watch([storeId, startDate, endDate], refresh);

async function refresh() {
  if (!startDate.value || !endDate.value) return;
  loading.value = true;
  errMsg.value = "";
  const params = { start: startDate.value, end: endDate.value, store_id: storeId.value || undefined };
  try {
    const [s, d, t] = await Promise.all([getSummary(params), getDaily(params), getTopProducts(params)]);
    summary.value = s;
    daily.value = d.days || [];
    top.value = t.products || [];
    renderChart();
  } catch (err) {
    errMsg.value = err.message;
  } finally {
    loading.value = false;
  }
}

function renderChart() {
  if (!chart) return;
  chart.setOption({
    tooltip: { trigger: "axis" },
    legend: { data: ["净营业额", "有效订单数"] },
    grid: { left: 60, right: 60, top: 40, bottom: 30 },
    xAxis: { type: "category", data: daily.value.map((d) => d.date.slice(5)) },
    yAxis: [
      { type: "value", name: "营业额(元)" },
      { type: "value", name: "订单数" },
    ],
    series: [
      {
        name: "净营业额",
        type: "line",
        smooth: true,
        data: daily.value.map((d) => d.net_revenue),
        itemStyle: { color: "#2456d6" },
      },
      {
        name: "有效订单数",
        type: "bar",
        yAxisIndex: 1,
        data: daily.value.map((d) => d.orders),
        itemStyle: { color: "#9db8f0", opacity: 0.6 },
      },
    ],
  });
}

function fmtMoney(v) {
  if (v === null || v === undefined) return "—";
  return "¥" + Number(v).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtKpi(kpi) {
  const v = summary.value?.[kpi.key];
  if (v === null || v === undefined) return "—";
  return kpi.money ? fmtMoney(v) : Number(v).toLocaleString("zh-CN");
}

function label(key) {
  return REMOVED_LABELS[key] || key;
}
</script>

<template>
  <div>
    <div class="card" style="display: flex; gap: 12px; align-items: center; flex-wrap: wrap">
      <label>门店
        <select v-model="storeId">
          <option value="">全部门店</option>
          <option v-for="s in stores" :key="s.store_id" :value="s.store_id">
            {{ s.store_name }}（{{ s.store_id }}）
          </option>
        </select>
      </label>
      <label>起 <input type="date" v-model="startDate" :max="endDate" /></label>
      <label>止 <input type="date" v-model="endDate" :min="startDate" /></label>
      <button class="primary" :disabled="loading" @click="refresh">
        {{ loading ? "加载中…" : "查询" }}
      </button>
    </div>

    <div class="err-box" v-if="errMsg">{{ errMsg }}</div>

    <div class="kpi-row" v-if="summary">
      <div class="kpi" v-for="kpi in KPIS" :key="kpi.key">
        <div class="label">{{ kpi.label }}</div>
        <div class="value">
          {{ fmtKpi(kpi) }}<span class="unit" v-if="kpi.money && summary[kpi.key] !== null">元</span>
        </div>
      </div>
    </div>

    <div class="card">
      <h3>营业额趋势</h3>
      <div ref="chartEl" style="height: 320px"></div>
    </div>

    <div class="grid-2">
      <div class="card">
        <h3>Top 10 商品（按净营业额）</h3>
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>商品</th>
              <th>类别</th>
              <th class="num">净营业额</th>
              <th class="num">订单</th>
              <th class="num">销量</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(p, i) in top" :key="p.product_id">
              <td>{{ i + 1 }}</td>
              <td>{{ p.product_name }} <span class="hint">{{ p.product_id }}</span></td>
              <td>{{ p.product_category }}</td>
              <td class="num">{{ fmtMoney(p.net_revenue) }}</td>
              <td class="num">{{ p.orders }}</td>
              <td class="num">{{ p.qty }}</td>
            </tr>
            <tr v-if="!top.length">
              <td colspan="6" class="hint">该区间没有销售数据</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="card">
        <h3>数据质量（清洗报告）</h3>
        <template v-if="quality">
          <div class="quality-grid">
            <div class="kpi">
              <div class="label">原始明细行</div>
              <div class="value">{{ quality.cleaning_report.raw_rows }}</div>
            </div>
            <div class="kpi">
              <div class="label">清洗后保留</div>
              <div class="value" style="color: var(--green)">{{ quality.cleaning_report.kept_rows }}</div>
            </div>
            <div class="kpi">
              <div class="label">销售行 / 退款行</div>
              <div class="value">
                {{ quality.cleaning_report.kept_sales_rows }}
                <span class="unit">/ {{ quality.cleaning_report.kept_refund_rows }}</span>
              </div>
            </div>
            <div class="kpi">
              <div class="label">剔除合计</div>
              <div class="value" style="color: var(--red)">
                {{ Object.values(quality.cleaning_report.removed).reduce((a, b) => a + b, 0) }}
              </div>
            </div>
          </div>

          <h3 style="margin-top: 4px">剔除原因分布</h3>
          <div
            class="removed-row"
            v-for="(count, key) in quality.cleaning_report.removed"
            :key="key"
          >
            <span class="removed-key">{{ label(key) }}</span>
            <div
              class="removed-bar"
              :style="{
                width:
                  (count /
                    Math.max(1, Math.max(...Object.values(quality.cleaning_report.removed)))) *
                    100 +
                  '%',
              }"
            ></div>
            <span>{{ count }}</span>
          </div>

          <p class="hint" style="margin-top: 10px" v-if="quality.kb_warnings?.length">
            知识库告警：{{ quality.kb_warnings.join("；") }}
          </p>
          <p class="hint" style="margin-top: 10px">
            口径：按 KB-001 现行手册执行——退款行计入净营业额、金额为空剔除不回填 0、DD-MM-YYYY 按日在前解析。
          </p>
        </template>
      </div>
    </div>
  </div>
</template>
