import { useState } from "react";
import { apiJson } from "./api";

// Preserve the previous Echo / health exercises alongside the new application.
export default function LegacyDemo() {
  const [name, setName] = useState("");
  const [message, setMessage] = useState("");
  const [healthMessage, setHealthMessage] = useState("尚未检测");

  async function handleSend() {
    try {
      const data = await apiJson("/api/echo", { method: "POST", body: JSON.stringify({ name }) });
      setMessage(data.message);
    } catch (error) { setMessage("请求失败：" + error.message); }
  }
  async function checkHealth() {
    setHealthMessage("检测中…");
    try {
      const data = await apiJson("/health");
      setHealthMessage(data.status === "ok" ? "后端正常" : "后端响应异常");
    } catch (error) { setHealthMessage("检测失败：" + error.message); }
  }
  return (
    <details className="legacy panel">
      <summary>保留的学习练习 · Mini Echo 与健康检查</summary>
      <div className="legacy-content">
        <label htmlFor="legacy-name">名字</label>
        <input id="legacy-name" value={name} onChange={(event) => setName(event.target.value)} />
        <p>当前输入：{name || "—"}</p>
        <div className="button-row"><button type="button" onClick={handleSend}>Send</button><button type="button" onClick={checkHealth}>检测后端</button></div>
        <p aria-live="polite">连接状态：{healthMessage}</p><p aria-live="polite">后端返回：{message || "—"}</p>
      </div>
    </details>
  );
}
