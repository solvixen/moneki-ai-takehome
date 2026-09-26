// 统一的 API 客户端：开发走 Vite 代理，生产同源部署。
// 4xx 不重试，5xx 重试最多 3 次（指数退避），网络失败给出可读文案。
const BASE = "";

async function request(path, options = {}, retries = 0) {
  let resp;
  try {
    resp = await fetch(BASE + path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
  } catch (err) {
    if (retries > 0) {
      await new Promise((r) => setTimeout(r, 400));
      return request(path, options, retries - 1);
    }
    throw new Error("无法连接服务：请确认后端已启动（uvicorn kbqa.server:app，端口 8000）");
  }
  if (resp.status >= 500 && retries > 0) {
    await new Promise((r) => setTimeout(r, 400));
    return request(path, options, retries - 1);
  }
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const msg = body && body.error ? body.error : "请求失败（HTTP " + resp.status + "）";
    throw new Error(msg);
  }
  return body;
}

export function getHealth() {
  return request("/api/health");
}

export function getStores() {
  return request("/api/stores");
}

export function getSummary(params) {
  return request("/api/metrics/summary?" + new URLSearchParams(clean(params)));
}

export function getDaily(params) {
  return request("/api/metrics/daily?" + new URLSearchParams(clean(params)));
}

export function getTopProducts(params) {
  return request("/api/metrics/top_products?" + new URLSearchParams(clean(params)));
}

export function getDataQuality() {
  return request("/api/data_quality");
}

export function postChat(sessionId, question) {
  return request("/api/chat", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, question }),
  });
}

export function getTrace(traceId) {
  return request("/api/trace/" + encodeURIComponent(traceId));
}

function clean(params) {
  const out = {};
  for (const [k, v] of Object.entries(params)) {
    if (v !== null && v !== undefined && v !== "") out[k] = v;
  }
  return out;
}
