import { useEffect, useRef, useState } from "react";
import { apiJson, apiRequest } from "./api";
import LegacyDemo from "./LegacyDemo";

const STATUS = { pass: "通过", risk: "风险", not_applicable: "不适用" };
const MAX_LENGTH = 100000;
const SAMPLE_PASS = `采购服务合同
甲方：星河科技有限公司
乙方：远山服务有限公司
服务内容：乙方向甲方提供设备维护服务。
合同金额：人民币 10000 元。
付款期限：验收后 30 日内付款。
解除合同须双方协商一致，并提前 30 日书面通知。
双方应依法承担各自的违约责任。
争议解决：双方先行协商，协商不成向约定的人民法院提起诉讼。
签署日期：2026 年 10 月 10 日。`;
const SAMPLE_RISK = SAMPLE_PASS
  .replace("解除合同须双方协商一致，并提前 30 日书面通知。", "甲方可随时解除合同，无需提前通知。")
  .replace("双方应依法承担各自的违约责任。", "甲方对服务中断造成的损失不承担任何责任。");

function displayTime(value) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).format(new Date(value));
}
function Badge({ status }) {
  return <span className={`badge badge-${status}`}>{STATUS[status] || status}</span>;
}
// 把片段拆成「前文 / 命中 / 后文」。后端位置是 Unicode 码点，所以用 Array.from 按码点切分。
function splitSnippet(item) {
  const snippet = item.snippet || item.text;
  const chars = Array.from(snippet);
  const length = Array.from(item.text).length;
  let offset = item.snippet_start == null ? -1 : item.start - item.snippet_start;
  if (offset < 0 || chars.slice(offset, offset + length).join("") !== item.text) {
    // 兼容没有 snippet_start 的旧记录。
    const index = snippet.indexOf(item.text);
    offset = index < 0 ? 0 : Array.from(snippet.slice(0, index)).length;
  }
  return [chars.slice(0, offset).join(""), chars.slice(offset, offset + length).join(""), chars.slice(offset + length).join("")];
}
function EvidenceSnippet({ item }) {
  const [before, hit, after] = splitSnippet(item);
  return <span className="evidence-snippet">{before}<mark>{hit}</mark>{after}</span>;
}
function SourceSnapshot({ review, evidence, markRef }) {
  // Python offsets are code points; JS string.slice otherwise counts UTF-16 units.
  const characters = Array.from(review.content);
  const start = evidence?.start ?? 0;
  const end = evidence?.end ?? 0;
  return (
    <section className="source-snapshot" aria-labelledby="source-title">
      <div className="section-heading compact">
        <div><h3 id="source-title">本次审查原文</h3><p>{review.filename} · 与当前结果绑定的已保存版本</p></div>
        {evidence && <span className="source-line">第 {evidence.line} 行</span>}
      </div>
      <pre className="reviewed-source" tabIndex={0}>{evidence ? <>{characters.slice(0, start).join("")}<mark ref={markRef}>{characters.slice(start, end).join("")}</mark>{characters.slice(end).join("")}</> : review.content}</pre>
    </section>
  );
}

function ResultCard({ result, draft, busy, onDraft, onSave, onEvidence }) {
  const status = draft?.status ?? result.final_status;
  const reason = draft?.reason ?? "";
  const changed = status !== result.final_status;
  return (
    <article className={`result-card result-${result.final_status}`}>
      <div className="result-top"><div className="rule-title"><span className="rule-id">{result.rule_id}</span><h3>{result.name}</h3></div><div className="badge-row">{result.source === "llm" && <span className="badge badge-ai" title="关键词/正则未命中，由大模型做语义判断">AI 判断 · 需人工复核</span>}<Badge status={result.final_status} /></div></div>
      <p className="rule-description">{result.description}</p><p className="rule-message">{result.message}</p>
      <div className="evidence-block">
        <span className="field-caption">原文证据</span>
        {result.evidence.length ? result.evidence.map((item, index) => <button type="button" className="evidence-link" key={`${item.start}-${index}`} onClick={() => onEvidence(item)}><span className="evidence-location">L{item.line} ↗</span><EvidenceSnippet item={item} /></button>) : <p className="no-evidence">没有可定位的原文证据，判断依据见上方说明。</p>}
      </div>
      <div className="review-edit">
        <div className="edit-heading"><span>人工复核</span><span className="muted">规则原判：{STATUS[result.original_status]}</span></div>
        <div className="edit-fields">
          <label className="sr-only" htmlFor={`status-${result.rule_id}`}>{result.name}复核状态</label>
          <select id={`status-${result.rule_id}`} disabled={busy} value={status} onChange={(event) => onDraft({ status: event.target.value, reason })}>{Object.entries(STATUS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
          <label className="sr-only" htmlFor={`reason-${result.rule_id}`}>{result.name}修改原因</label>
          <input id={`reason-${result.rule_id}`} value={reason} disabled={busy} maxLength={1000} placeholder="修改原因（必填）" onChange={(event) => onDraft({ status, reason: event.target.value })} />
          <button type="button" className="save-button" disabled={busy || !changed || !reason.trim()} onClick={onSave}>保存复核</button>
        </div>
        {changed && <p className="draft-hint">状态修改尚未保存。保存后会记录操作人、原因和修改时间。</p>}
      </div>
    </article>
  );
}

export default function App() {
  const [content, setContent] = useState("");
  const [filename, setFilename] = useState("未命名合同.txt");
  const [review, setReview] = useState(null);
  const [history, setHistory] = useState([]);
  const [historyError, setHistoryError] = useState("");
  const [historyLoading, setHistoryLoading] = useState(true);
  const [drafts, setDrafts] = useState({});
  const [actor, setActor] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [evidence, setEvidence] = useState(null);
  const [focusTick, setFocusTick] = useState(0);
  const busyRef = useRef(false);
  const historyRequest = useRef(0);
  const markRef = useRef(null);
  const fileRef = useRef(null);
  const resultsRef = useRef(null);

  async function loadHistory(signal) {
    const requestId = ++historyRequest.current;
    setHistoryLoading(true);
    try {
      const data = await apiJson("/reviews", { signal });
      if (requestId === historyRequest.current) { setHistory(data.items); setHistoryError(""); }
    } catch (problem) {
      if (problem.name !== "AbortError" && requestId === historyRequest.current) setHistoryError(problem.message);
    } finally { if (requestId === historyRequest.current) setHistoryLoading(false); }
  }
  useEffect(() => {
    const controller = new AbortController();
    loadHistory(controller.signal);
    return () => controller.abort();
  }, []);
  useEffect(() => {
    if (evidence) markRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [evidence, focusTick]);
  function focusEvidence(item) { setEvidence(item); setFocusTick((tick) => tick + 1); }

  async function runAction(label, action) {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(label); setError(""); setNotice("");
    try { await action(); }
    catch (problem) { setError(problem.message); }
    finally { busyRef.current = false; setBusy(""); }
  }
  function acceptReview(nextReview) { setReview(nextReview); setDrafts({}); setEvidence(null); }
  const unsaved = review?.results.some((result) => drafts[result.rule_id] && (drafts[result.rule_id].status !== result.final_status || drafts[result.rule_id].reason.trim())) ?? false;
  function canDiscardDrafts() {
    return !unsaved || window.confirm("当前有未保存的复核草稿，继续将丢弃这些草稿。确定继续吗？");
  }
  function loadSample(risky) {
    setContent(risky ? SAMPLE_RISK : SAMPLE_PASS); setFilename(risky ? "risk.txt" : "pass.txt"); setError("");
    setNotice("样例已载入，点击「开始审查」由后端执行规则。");
    if (fileRef.current) fileRef.current.value = "";
  }
  async function uploadFile(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    await runAction("正在读取文件…", async () => {
      if (!/\.txt$/i.test(file.name)) throw new Error("当前支持 UTF-8 编码的 .txt 合同文件。");
      if (Array.from(file.name).length > 200) throw new Error("文件名不能超过 200 个字符，请重命名后上传。");
      if (file.size > 1024 * 1024) throw new Error("文件不能超过 1 MB。");
      let text;
      try { text = new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer()).replace(/^\uFEFF/, "").replace(/\r\n?/g, "\n"); }
      catch { throw new Error("文件不是有效的 UTF-8 文本，请另存为 UTF-8 编码后上传。"); }
      if (!text.trim()) throw new Error("合同内容不能为空或只有空白。");
      if (Array.from(text).length > MAX_LENGTH) throw new Error("合同不能超过 100,000 个字符。");
      setContent(text); setFilename(file.name); setNotice("文件已读取，请确认文本后开始审查。");
    });
    event.target.value = "";
  }
  async function startReview() {
    if (!canDiscardDrafts()) return;
    await runAction("正在执行规则审查…", async () => {
      if (!content.trim()) throw new Error("请先上传合同或输入合同文本。");
      if (Array.from(content).length > MAX_LENGTH) throw new Error("合同不能超过 100,000 个字符。");
      const data = await apiJson("/review", { method: "POST", body: JSON.stringify({ filename, content }) });
      acceptReview(data); setNotice("审查完成，结果已保存。可以逐条人工复核并导出报告。");
      await loadHistory(); resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }
  async function openReview(id) {
    if (review?.id === id || !canDiscardDrafts()) return;
    await runAction("正在读取历史审查…", async () => {
      acceptReview(await apiJson(`/reviews/${id}`));
      setNotice("已打开历史记录。左侧输入区仍保留你的待审查文本。");
    });
  }
  async function saveResult(result) {
    await runAction("正在保存人工复核…", async () => {
      if (!actor.trim()) throw new Error("请先填写复核人姓名；此演示不包含登录身份认证。");
      const draft = drafts[result.rule_id];
      if (!draft?.reason.trim()) throw new Error("请填写修改原因。");
      try {
        const data = await apiJson(`/reviews/${review.id}/results/${encodeURIComponent(result.rule_id)}`, {
          method: "PATCH", body: JSON.stringify({ status: draft.status, actor: actor.trim(), reason: draft.reason.trim(), expected_status: result.final_status }),
        });
        setReview(data); setDrafts((previous) => { const next = { ...previous }; delete next[result.rule_id]; return next; });
        setNotice("复核已保存，审计记录已同步写入数据库。"); await loadHistory();
      } catch (problem) {
        if (problem.status === 409) {
          setReview(await apiJson(`/reviews/${review.id}`)); await loadHistory();
          throw new Error("该记录已被其他操作修改，已刷新最新结果。你的复核草稿仍在，但尚未保存，请重新确认后保存。");
        }
        throw problem;
      }
    });
  }
  async function exportReport(format) {
    await runAction("正在生成报告…", async () => {
      const response = await apiRequest("/report", { method: "POST", body: JSON.stringify({ review_id: review.id, format }) });
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a"); link.href = url; link.download = `contract-review-${review.id}.${format === "json" ? "json" : "md"}`;
      document.body.appendChild(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 10000);
      setNotice(unsaved ? "已发起报告下载。报告仅包含已保存的结果，不包含尚未保存的复核草稿。" : "已发起报告下载，包含原始判断、最终状态及完整审计记录。");
    });
  }

  const counts = { pass: 0, risk: 0, not_applicable: 0 };
  review?.results.forEach((result) => { counts[result.final_status] += 1; });
  const contentLength = Array.from(content).length;
  const sourceChanged = review && (content !== review.content || filename !== review.filename);

  return (
    <div className="app-shell">
      <header className="topbar"><a href="#" className="brand"><span className="brand-mark" aria-hidden="true">C</span><span>Contract<span className="brand-light">Check</span><small>合同规则审查工作台</small></span></a><span className="local-label"><span />本地训练版 · SQLite 持久化</span></header>
      <main>
        <section className="hero"><div><p className="eyebrow">CONTRACT REVIEW WORKSPACE</p><h1>让每一次审查，都有据可循。</h1><p className="hero-description">配置驱动的规则检查，结合人工复核与可追溯的审计记录。</p></div><div className="workflow" aria-label="审查流程"><span><b>01</b>导入合同</span><i>→</i><span><b>02</b>规则审查</span><i>→</i><span><b>03</b>复核与导出</span></div></section>
        <div className="disclaimer"><span aria-hidden="true">ⓘ</span> 本工具使用演示规则做关键词与正则匹配；默认不调用大模型。开启 AI 兜底后，未命中的部分规则会交给大模型做语义判断，并标注「AI 判断 · 需人工复核」。结果不构成法律意见，未发现风险不代表合同没有风险。</div>
        <div className="feedback-region" aria-live="polite" aria-atomic="true">{busy && <div className="feedback feedback-info"><span className="spinner" />{busy}</div>}{error && <div className="feedback feedback-error" role="alert">{error}</div>}{!busy && notice && <div className="feedback feedback-success">{notice}</div>}</div>
        <div className="workspace">
          <aside className="input-column">
            <section className="panel source-panel">
              <div className="section-heading"><div><p className="eyebrow">STEP 01</p><h2>准备合同</h2></div><span className="section-icon" aria-hidden="true">↥</span></div>
              <input className="sr-only" ref={fileRef} id="contract-file" type="file" accept=".txt,text/plain" disabled={Boolean(busy)} onChange={uploadFile} />
              <button type="button" className="upload-area" disabled={Boolean(busy)} onClick={() => fileRef.current?.click()}><span className="upload-symbol" aria-hidden="true">＋</span><strong>选择合同文件</strong><span>UTF-8 · TXT · 最大 1 MB</span></button>
              <div className="sample-row"><span>快速体验</span><button type="button" disabled={Boolean(busy)} onClick={() => loadSample(false)}>全通过样例</button><button type="button" disabled={Boolean(busy)} onClick={() => loadSample(true)}>两处风险样例</button></div>
              <label className="input-label" htmlFor="contract-text">待审查文本 <span title={filename}>{filename}</span></label>
              <textarea id="contract-text" value={content} onChange={(event) => setContent(event.target.value)} disabled={Boolean(busy)} placeholder="上传合同，或在这里粘贴合同全文…" rows={14} />
              <div className={`character-count ${contentLength > MAX_LENGTH ? "over-limit" : ""}`}>{contentLength.toLocaleString()} / 100,000 字符</div>
              {sourceChanged && <p className="source-warning">右侧展示的是「{review.filename}」的已保存结果。修改输入后需重新审查，结果才会更新。</p>}
              <button type="button" className="button-primary review-button" disabled={Boolean(busy) || !content.trim() || contentLength > MAX_LENGTH} onClick={startReview}>开始审查 <span aria-hidden="true">→</span></button><p className="helper-text">提交后由后端执行规则，结果自动保存。</p>
            </section>
            <section className="panel history-panel">
              <div className="section-heading compact"><div><h2>最近审查</h2><p>持久化保存 · 最近 20 条</p></div><button type="button" className="text-button" disabled={Boolean(busy) || historyLoading} onClick={() => loadHistory()}>刷新</button></div>
              {historyLoading && <p className="helper-text">正在读取历史记录…</p>}{historyError && <p className="inline-error" role="alert">历史记录加载失败：{historyError}</p>}{!historyLoading && !historyError && history.length === 0 && <p className="history-empty">还没有审查记录。完成首次审查后，会保存在这里。</p>}
              <div className="history-list">{history.map((item) => <button type="button" key={item.id} className={`history-item ${review?.id === item.id ? "selected" : ""}`} disabled={Boolean(busy)} onClick={() => openReview(item.id)} aria-current={review?.id === item.id ? "true" : undefined}><span className="history-file" title={item.filename}>{item.filename}</span><span className="history-meta"><time dateTime={item.updated_at}>{displayTime(item.updated_at)}</time><span className={item.risk_count ? "history-risk" : "history-pass"}>{item.risk_count ? `${item.risk_count} 项风险` : "无规则风险"}</span></span></button>)}</div>
            </section>
          </aside>
          <section className="results-column" ref={resultsRef} aria-labelledby="results-title">
            <div className="panel results-panel">
              <div className="section-heading"><div><p className="eyebrow">STEP 02 — 03</p><h2 id="results-title">审查与复核</h2></div>{review && <span className="saved-label">已保存</span>}</div>
              {!review ? <div className="empty-state"><div className="empty-illustration" aria-hidden="true"><span>✓</span><i /><i /><i /></div><h3>从一份合同开始</h3><p>导入合同并开始审查，规则结果、命中证据<br />与人工复核记录会显示在这里。</p><div className="empty-features"><span>关键词 + 正则</span><span>原文定位</span><span>全程审计</span></div></div> : <>
                <div className="review-meta"><strong title={review.filename}>{review.filename}</strong><span>审查编号 #{review.id} · {displayTime(review.created_at)}</span></div>
                <div className="summary-grid"><div><span>检查规则</span><strong>{review.results.length}<small>项</small></strong></div><div className="summary-pass"><span>通过</span><strong>{counts.pass}<small>项</small></strong></div><div className="summary-risk"><span>风险</span><strong>{counts.risk}<small>项</small></strong></div><div><span>不适用</span><strong>{counts.not_applicable}<small>项</small></strong></div></div>
                <div className="reviewer-field"><label htmlFor="reviewer-name">复核人</label><input id="reviewer-name" value={actor} disabled={Boolean(busy)} maxLength={80} placeholder="填写姓名后可保存人工修改" onChange={(event) => setActor(event.target.value)} /><small>演示身份，未做登录认证</small></div>
                <div className="results-list">{review.results.map((result) => <ResultCard key={result.rule_id} result={result} draft={drafts[result.rule_id]} busy={Boolean(busy)} onDraft={(draft) => setDrafts((previous) => ({ ...previous, [result.rule_id]: draft }))} onSave={() => saveResult(result)} onEvidence={focusEvidence} />)}</div>
                <SourceSnapshot review={review} evidence={evidence} markRef={markRef} />
                <section className="export-section"><div><h3>导出审查报告</h3><p>包含原始判断、最终状态、证据与审计记录。</p>{unsaved && <p className="draft-hint">存在未保存的复核草稿，导出不会包含这些草稿。</p>}</div><div className="button-row"><button type="button" disabled={Boolean(busy)} onClick={() => exportReport("json")}>↓ JSON</button><button type="button" className="button-primary" disabled={Boolean(busy)} onClick={() => exportReport("markdown")}>↓ Markdown</button></div></section>
              </>}
            </div>
            {review && <section className="panel audit-panel"><div className="section-heading compact"><div><h2>审计轨迹</h2><p>每次已保存的人工状态修改都会留下记录。</p></div><span className="audit-count">{review.audit.length} 条</span></div>{review.audit.length ? <ol className="audit-list">{review.audit.map((item) => <li key={item.id}><div className="audit-heading"><strong>{item.actor}</strong><time dateTime={item.created_at}>{displayTime(item.created_at)}</time></div><p>{item.rule_name} <span className="audit-transition">{STATUS[item.old_status]} → {STATUS[item.new_status]}</span></p><p className="audit-reason">{item.reason}</p></li>)}</ol> : <p className="history-empty">暂无人工修改。修改规则状态并保存后，记录会出现在这里。</p>}</section>}
          </section>
        </div>
        <LegacyDemo />
      </main>
      <footer><span>ContractCheck · 全栈开发训练</span><span>React / FastAPI / SQLite</span></footer>
    </div>
  );
}
