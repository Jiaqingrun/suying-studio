import { FormEvent, useEffect, useEffectEvent, useRef, useState } from "react";
import { api, API_BASE } from "./api";
import { AssistantMarkdown } from "./AssistantMarkdown";

type ToolCard = {
  id: string;
  name: string;
  label: string;
  status: "running" | "completed" | "error";
  text: string;
};

type ChatMsg = {
  id: string;
  role: "user" | "assistant";
  text: string;
  tools?: ToolCard[];
};

type Props = {
  notify: (text: string, kind?: "ok" | "err" | "info" | "warn") => void;
  keyConfigured: boolean;
  onGoOps: () => void;
  onConfirmReset?: () => Promise<boolean>;
};

const NOISE_STATUS = new Set(["RUNNING", "FINISHED", "running", "finished", "COMPLETED", "completed"]);

function newId(prefix: string) {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
}

function upsertTool(list: ToolCard[], next: ToolCard): ToolCard[] {
  const key = next.name || next.label;
  const idx = list.findIndex((t) => (t.name || t.label) === key);
  if (idx < 0) return [...list, next];
  const copy = list.slice();
  copy[idx] = { ...copy[idx], ...next, id: copy[idx].id };
  return copy;
}

function ToolCards({ tools }: { tools: ToolCard[] }) {
  if (!tools.length) return null;
  return (
    <div className="cx-tools">
      {tools.map((t) => (
        <div key={t.id} className={`cx-tool is-${t.status}`} role="status">
          <span className="cx-tool-dot" aria-hidden />
          <span className="cx-tool-label">{t.label || t.name || "工具"}</span>
          <span className="cx-tool-status">
            {t.status === "running" ? "运行中" : t.status === "error" ? "失败" : "完成"}
          </span>
        </div>
      ))}
    </div>
  );
}

export function AssistantChat({ notify, keyConfigured, onGoOps, onConfirmReset }: Props) {
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [liveStatus, setLiveStatus] = useState("");
  const [draft, setDraft] = useState("");
  const [showDraft, setShowDraft] = useState(false);
  const [draftTools, setDraftTools] = useState<ToolCard[]>([]);

  const logRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const draftBufRef = useRef("");
  const toolsRef = useRef<ToolCard[]>([]);
  const rafRef = useRef<number | null>(null);
  const stickBottomRef = useRef(true);
  const abortRef = useRef<AbortController | null>(null);
  const statusTimerRef = useRef<number | null>(null);
  const streamingRef = useRef(false);

  const flushDraft = useEffectEvent(() => {
    rafRef.current = null;
    setDraft(draftBufRef.current);
  });

  const queueDraftChunk = useEffectEvent((chunk: string) => {
    if (!chunk) return;
    draftBufRef.current += chunk;
    if (rafRef.current == null) {
      rafRef.current = window.requestAnimationFrame(() => flushDraft());
    }
  });

  const pushTool = useEffectEvent((ev: Record<string, unknown>) => {
    const statusRaw = String(ev.status || "running");
    const status: ToolCard["status"] =
      statusRaw === "completed" || statusRaw === "error" ? statusRaw : "running";
    const card: ToolCard = {
      id: newId("tool"),
      name: String(ev.name || ""),
      label: String(ev.label || ev.name || "工具"),
      status,
      text: String(ev.text || ""),
    };
    toolsRef.current = upsertTool(toolsRef.current, card);
    setDraftTools(toolsRef.current);
  });

  const scrollIfNeeded = useEffectEvent(() => {
    const el = logRef.current;
    if (!el || !stickBottomRef.current) return;
    el.scrollTop = el.scrollHeight;
  });

  function autosize() {
    const el = taRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(160, Math.max(52, el.scrollHeight))}px`;
  }

  async function refresh() {
    try {
      const s = await api.cursorChatGet();
      const list = (s.messages || []).map((m, i) => ({
        id: `hist-${i}-${String(m.at || "")}`,
        role: (m.role === "user" ? "user" : "assistant") as "user" | "assistant",
        text: String(m.text || ""),
      }));
      setMessages(list);
      if (!streamingRef.current) {
        setBusy(false);
        setLiveStatus("");
        setShowDraft(false);
        setDraft("");
        draftBufRef.current = "";
        toolsRef.current = [];
        setDraftTools([]);
      }
    } catch {
      /* older engine */
    }
  }

  useEffect(() => {
    void refresh();
    void api.cursorChatCancel().catch(() => undefined);
    return () => {
      abortRef.current?.abort();
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
      if (statusTimerRef.current != null) window.clearTimeout(statusTimerRef.current);
    };
  }, []);

  useEffect(() => {
    scrollIfNeeded();
  }, [messages, draft, liveStatus, showDraft, draftTools]);

  function onLogScroll() {
    const el = logRef.current;
    if (!el) return;
    const gap = el.scrollHeight - el.scrollTop - el.clientHeight;
    stickBottomRef.current = gap < 80;
  }

  function setStatusThrottled(text: string) {
    if (statusTimerRef.current != null) {
      window.clearTimeout(statusTimerRef.current);
      statusTimerRef.current = null;
    }
    if (!text) {
      setLiveStatus("");
      return;
    }
    statusTimerRef.current = window.setTimeout(() => {
      setLiveStatus(text);
      statusTimerRef.current = null;
    }, 80);
  }

  async function onSubmit(e?: FormEvent) {
    e?.preventDefault();
    const text = input.trim();
    if (!text || busy || streamingRef.current) return;
    if (!keyConfigured) {
      notify("请先在运维页配置 Cursor API Key", "err");
      onGoOps();
      return;
    }

    setInput("");
    if (taRef.current) {
      taRef.current.style.height = "52px";
    }
    stickBottomRef.current = true;
    setMessages((prev) => [...prev, { id: newId("u"), role: "user", text }]);
    draftBufRef.current = "";
    toolsRef.current = [];
    setDraft("");
    setDraftTools([]);
    setShowDraft(true);
    streamingRef.current = true;
    setBusy(true);
    setLiveStatus("连接中…");

    const ac = new AbortController();
    abortRef.current = ac;

    try {
      const res = await fetch(`${API_BASE}/cursor/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify({ message: text, session_id: "default" }),
        signal: ac.signal,
      });
      if (!res.ok) {
        throw new Error((await res.text()) || res.statusText);
      }
      if (!res.body) throw new Error("引擎未返回流式响应体");

      const reader = res.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      let donePayload: Record<string, unknown> | null = null;
      let streamError: string | null = null;

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n\n");
        buffer = parts.pop() || "";
        let stopStream = false;
        for (const part of parts) {
          const line = part
            .split("\n")
            .map((l) => l.trim())
            .find((l) => l.startsWith("data:"));
          if (!line) continue;
          const raw = line.slice(5).trim();
          if (!raw || raw === "[DONE]") continue;
          let ev: Record<string, unknown>;
          try {
            ev = JSON.parse(raw) as Record<string, unknown>;
          } catch {
            continue;
          }
          const t = String(ev.type || "");
          if (t === "start") {
            setStatusThrottled("生成中…");
          } else if (t === "delta") {
            queueDraftChunk(String(ev.text || ""));
            setStatusThrottled("");
          } else if (t === "tool") {
            pushTool(ev);
            const st = String(ev.text || "");
            if (st && !NOISE_STATUS.has(st)) setStatusThrottled(st);
          } else if (t === "status") {
            const st = String(ev.text || "");
            if (st && !NOISE_STATUS.has(st)) setStatusThrottled(st);
          } else if (t === "done") {
            donePayload = ev;
            stopStream = true;
          } else if (t === "error") {
            streamError = String(ev.message || "流式错误");
            stopStream = true;
          }
        }
        if (stopStream) {
          try {
            await reader.cancel();
          } catch {
            /* ignore */
          }
          break;
        }
      }

      if (rafRef.current != null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      const finalText = String(
        (donePayload?.reply as string | undefined) || draftBufRef.current || "",
      ).trim();
      const finalTools = toolsRef.current.map((t) =>
        t.status === "running" ? { ...t, status: "completed" as const } : t,
      );
      setShowDraft(false);
      setDraft("");
      setDraftTools([]);
      draftBufRef.current = "";
      toolsRef.current = [];
      if (streamError) throw new Error(streamError);
      if (!donePayload) throw new Error("流式结束但未收到完成事件");
      if (finalText || finalTools.length) {
        setMessages((prev) => [
          ...prev,
          {
            id: newId("a"),
            role: "assistant",
            text: finalText || "（本轮无文本回复）",
            tools: finalTools.length ? finalTools : undefined,
          },
        ]);
      }
      setLiveStatus("");
    } catch (err) {
      if ((err as { name?: string })?.name === "AbortError") {
        const partial = draftBufRef.current.trim();
        const tools = toolsRef.current.slice();
        setShowDraft(false);
        setDraft("");
        setDraftTools([]);
        draftBufRef.current = "";
        toolsRef.current = [];
        if (partial || tools.length) {
          setMessages((prev) => [
            ...prev,
            {
              id: newId("a"),
              role: "assistant",
              text: partial ? `${partial}\n\n（已停止）` : "（已停止）",
              tools: tools.length ? tools : undefined,
            },
          ]);
        }
        notify("已停止生成", "info");
      } else {
        notify(String(err), "err");
        setShowDraft(false);
        setDraft("");
        setDraftTools([]);
        draftBufRef.current = "";
        toolsRef.current = [];
        await refresh();
      }
    } finally {
      streamingRef.current = false;
      abortRef.current = null;
      setBusy(false);
      setLiveStatus("");
    }
  }

  function onStop() {
    abortRef.current?.abort();
    streamingRef.current = false;
    setBusy(false);
    setLiveStatus("");
    setShowDraft(false);
    setDraftTools([]);
    void api.cursorChatCancel().catch(() => undefined);
  }

  async function onReset() {
    const ok = onConfirmReset
      ? await onConfirmReset()
      : false;
    if (!ok) return;
    abortRef.current?.abort();
    streamingRef.current = false;
    setBusy(true);
    try {
      await api.cursorChatCancel().catch(() => undefined);
      const s = await api.cursorChatReset();
      setMessages(
        (s.messages || []).map((m, i) => ({
          id: `hist-${i}`,
          role: (m.role === "user" ? "user" : "assistant") as "user" | "assistant",
          text: String(m.text || ""),
        })),
      );
      setLiveStatus("");
      setShowDraft(false);
      setDraft("");
      setDraftTools([]);
      draftBufRef.current = "";
      toolsRef.current = [];
      notify("已开启新对话循环", "ok");
    } catch (err) {
      notify(String(err), "err");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="assistant-panel">
      <header className="assistant-top">
        <div className="cx-top">
          <div className="cx-top-brand">
            <span className="cx-orb" aria-hidden />
            <div className="cx-top-titles">
              <span className="cx-chip">Agent</span>
              <h2>速影助手</h2>
            </div>
          </div>
          <div className="cx-top-actions">
            <button type="button" className="cx-ghost" onClick={() => refresh()} disabled={busy}>
              刷新
            </button>
            <button type="button" className="cx-ghost" onClick={() => onReset()} disabled={busy}>
              新对话
            </button>
          </div>
        </div>
        {!keyConfigured && (
          <div className="cx-banner">
            尚未配置 API Key
            <button type="button" className="cx-ghost cx-ghost-accent" onClick={onGoOps}>
              去运维填写
            </button>
          </div>
        )}
      </header>

      <div className="assistant-log" ref={logRef} onScroll={onLogScroll}>
        <div className="cx-thread">
          {messages.length === 0 && !showDraft && !busy && (
            <div className="cx-empty">
              <div className="cx-empty-orb" aria-hidden />
              <h3>有什么可以帮你的？</h3>
              <p>例如：总结片库状态、检查今日任务、排查发布问题</p>
            </div>
          )}
          {messages.map((m) => (
            <article
              key={m.id}
              className={`cx-msg cx-msg-enter ${m.role === "user" ? "is-user" : "is-bot"}`}
            >
              {m.role === "assistant" ? (
                <div className="cx-avatar" aria-hidden>
                  S
                </div>
              ) : null}
              <div className="cx-msg-body">
                {m.role === "user" ? <div className="cx-msg-label">你</div> : null}
                {m.role === "assistant" && m.tools?.length ? <ToolCards tools={m.tools} /> : null}
                {m.role === "assistant" ? (
                  <AssistantMarkdown text={m.text} />
                ) : (
                  <div className="cx-msg-text">{m.text}</div>
                )}
              </div>
            </article>
          ))}
          {showDraft && (
            <article className="cx-msg is-bot is-streaming cx-msg-enter">
              <div className="cx-avatar is-pulse" aria-hidden>
                S
              </div>
              <div className="cx-msg-body">
                {draftTools.length > 0 ? <ToolCards tools={draftTools} /> : null}
                {!draft && !draftTools.length ? (
                  <div className="cx-thinking">
                    <span className="cx-thinking-dots" aria-hidden>
                      <i />
                      <i />
                      <i />
                    </span>
                    <span>{liveStatus || "思考中…"}</span>
                  </div>
                ) : draft ? (
                  <AssistantMarkdown text={draft} streaming />
                ) : null}
              </div>
            </article>
          )}
        </div>
      </div>

      <footer className="assistant-footer">
        {liveStatus && busy ? <p className="cx-live">{liveStatus}</p> : null}
        <form
          className="cx-composer"
          onSubmit={(e) => {
            e.preventDefault();
            void onSubmit();
          }}
        >
          <textarea
            ref={taRef}
            rows={2}
            value={input}
            disabled={busy}
            placeholder="问速影助手任何问题…"
            onChange={(e) => {
              setInput(e.target.value);
              autosize();
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                if (!busy) void onSubmit();
              }
            }}
          />
          <div className="cx-composer-foot">
            <span className="cx-composer-hint">⌘↩ 发送 · 回车换行</span>
            {busy ? (
              <button type="button" className="cx-send is-stop" onClick={onStop}>
                停止
              </button>
            ) : (
              <button
                type="button"
                className="cx-send"
                disabled={!input.trim()}
                onClick={() => void onSubmit()}
              >
                发送
              </button>
            )}
          </div>
        </form>
      </footer>
    </section>
  );
}
