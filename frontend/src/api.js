export const API_BASE = (import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

export async function apiRequest(path, options = {}) {
  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers: { ...(options.body ? { "Content-Type": "application/json" } : {}), ...options.headers },
    });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new Error("无法连接后端，请确认 FastAPI 已启动，且地址与 CORS 配置正确。");
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    // 校验错误带上字段名，例如「filename：String should have at most 200 characters」。
    const detail = Array.isArray(payload.detail)
      ? payload.detail.map((item) => [item.loc?.slice(1).join("."), item.msg].filter(Boolean).join("：")).join("；")
      : payload.detail;
    const error = new Error(typeof detail === "string" ? detail : `请求失败（HTTP ${response.status}）`);
    error.status = response.status;
    throw error;
  }
  return response;
}

export async function apiJson(path, options = {}) {
  return (await apiRequest(path, options)).json();
}
