import {
  DialogButton,
  Dropdown,
  Focusable,
  Navigation,
  QuickAccessTab,
  Spinner,
  TextField,
  useParams,
} from "@decky/ui";
import { addEventListener, openFilePicker, removeEventListener, toaster } from "@decky/api";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  getSession,
  Job,
  JOB_EVENT,
  JobEvent,
  Message,
  recentFolders,
  sendPrompt,
  Session,
  stopJob,
} from "./backend";
import { SessionRemoteControl, useRemote } from "./components/RemoteControl";

export const ROUTE = "/claude-sessions/:sessionId";
export const sessionPath = (id: string) => `/claude-sessions/${id}`;
export const NEW_SESSION = "new";

// Folder to preselect on the next new session page, set by the panel's "New session here".
let newSessionFolder = "";
export function setNewSessionFolder(cwd: string) {
  newSessionFolder = cwd;
}

// B leaves the session page and reopens the Decky tab of the Quick Access
// Menu, where the plugin panel was, instead of falling back to Steam's
// previous page.
function backToPanel() {
  Navigation.NavigateBack();
  Navigation.OpenQuickAccessMenu(QuickAccessTab.Decky);
}

const HEADER_HEIGHT = 40;
const FOOTER_HEIGHT = 48;

const COLORS: Record<Message["role"], string> = {
  user: "#2b4a6b",
  assistant: "#2a2d34",
  tool: "#1d1f24",
};

function MessageBubble({ message }: { message: Message }) {
  const isTool = message.kind !== "text";
  return (
    <Focusable
      noFocusRing={false}
      style={{
        background: COLORS[message.role],
        borderRadius: 6,
        padding: isTool ? "4px 10px" : "8px 12px",
        margin: "6px 0",
        marginLeft: message.role === "user" ? "15%" : 0,
        marginRight: message.role === "user" ? 0 : isTool ? "10%" : "5%",
        whiteSpace: "pre-wrap",
        wordBreak: "break-word",
        fontFamily: isTool ? "monospace" : undefined,
        fontSize: isTool ? 12 : 14,
        color: message.error ? "#ff8a80" : isTool ? "#9aa0a6" : "#e8eaed",
        opacity: isTool ? 0.85 : 1,
      }}
    >
      {message.kind === "tool_use" ? `▸ ${message.text}` : message.text}
    </Focusable>
  );
}

export default function SessionPage() {
  const { sessionId: routeId } = useParams<{ sessionId: string }>();
  const [sessionId, setSessionId] = useState(routeId === NEW_SESSION ? "" : routeId);
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const [job, setJob] = useState<Job | null>(null);
  const [prompt, setPrompt] = useState("");
  const [folders, setFolders] = useState<string[]>([]);
  const [cwd, setCwd] = useState("");
  const [sending, setSending] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const jobRef = useRef<Job | null>(null);
  jobRef.current = job;

  const load = useCallback(async (id: string) => {
    if (!id) {
      setLoading(false);
      return;
    }
    const s = await getSession(id, 200);
    setSession(s);
    setJob(s?.running_job ?? null);
    setLoading(false);
  }, []);

  useEffect(() => {
    load(sessionId);
    if (!sessionId) {
      const preset = newSessionFolder;
      newSessionFolder = "";
      recentFolders().then((f) => {
        const list = preset && !f.includes(preset) ? [preset, ...f] : f;
        setFolders(list);
        setCwd((c) => c || preset || list[0] || "");
      });
    }
  }, []);

  useEffect(() => {
    const listener = addEventListener<[JobEvent]>(JOB_EVENT, (ev) => {
      if (!jobRef.current || ev.job_id !== jobRef.current.id) return;
      if (ev.type === "init") {
        setSessionId(ev.session_id);
        setJob((j) => j && { ...j, session_id: ev.session_id });
      } else if (ev.type === "messages") {
        setJob((j) => j && { ...j, messages: [...j.messages, ...ev.messages] });
      } else if (ev.type === "end") {
        setJob((j) => j && { ...j, status: ev.job.status });
        if (ev.job.status === "error") {
          toaster.toast({ title: "Claude", body: ev.job.error ?? "The prompt failed." });
        }
        const id = ev.job.session_id ?? "";
        setSessionId(id);
        // The transcript on disk now has the whole turn; reload it and drop the live copy.
        load(id).then(() => setJob(null));
      }
    });
    return () => removeEventListener(JOB_EVENT, listener);
  }, [load]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [session, job?.messages.length]);

  const send = async () => {
    const text = prompt.trim();
    if (!text || sending) return;
    setSending(true);
    const res = await sendPrompt(sessionId, cwd, text);
    setSending(false);
    if (res.error || !res.job) {
      toaster.toast({ title: "Claude", body: res.error ?? "Couldn't send the prompt." });
      return;
    }
    setPrompt("");
    jobRef.current = res.job;
    setJob(res.job);
  };

  const browse = async () => {
    try {
      const res = await openFilePicker(1, cwd || folders[0] || "/home", false, true);
      setCwd(res.realpath || res.path);
      if (!folders.includes(res.path)) setFolders([res.path, ...folders]);
    } catch {
      // picker cancelled
    }
  };

  const { active: onRemote } = useRemote(sessionId);
  const running = job?.status === "running";
  const messages = [...(session?.messages ?? []), ...(job ? job.messages : [])];
  const title = session?.title ?? (sessionId ? "Session" : "New Claude session");
  const subtitle = session?.cwd || cwd;

  return (
    <Focusable
      onCancel={(e) => {
        e.stopPropagation();
        backToPanel();
      }}
      onCancelActionDescription="Back to Claude Sessions"
      style={{
        // Leave room for Steam's header (40px) and its bottom button bar (~40px),
        // which is drawn over route pages in Game Mode.
        marginTop: HEADER_HEIGHT,
        height: `calc(100% - ${HEADER_HEIGHT + FOOTER_HEIGHT}px)`,
        display: "flex",
        flexDirection: "column",
        padding: "0 24px",
        boxSizing: "border-box",
        color: "#e8eaed",
      }}
    >
      <Focusable style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 0 8px" }} flow-children="horizontal">
        <DialogButton style={{ width: 90, minWidth: 0, padding: "6px 10px" }} onClick={backToPanel}>
          ← Back
        </DialogButton>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 20, fontWeight: "bold", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
            {title}
          </div>
          <div style={{ fontSize: 12, opacity: 0.6 }}>{subtitle}</div>
        </div>
      </Focusable>
      {sessionId && <SessionRemoteControl sessionId={sessionId} busy={running || sending} />}

      <Focusable style={{ flex: 1, overflowY: "auto", paddingRight: 8 }}>
        {loading && <Spinner style={{ width: 32 }} />}
        {session?.truncated && <div style={{ opacity: 0.5, fontSize: 12 }}>Showing the latest messages only.</div>}
        {!loading && !sessionId && !job && (
          <div style={{ padding: "8px 0" }}>
            <div style={{ fontSize: 13, opacity: 0.7, marginBottom: 6 }}>Folder Claude will work in</div>
            <Focusable style={{ display: "flex", gap: 8 }} flow-children="horizontal">
              <div style={{ flex: 1 }}>
                <Dropdown
                  rgOptions={folders.map((f) => ({ data: f, label: f }))}
                  selectedOption={cwd}
                  onChange={(o) => setCwd(o.data)}
                  strDefaultLabel={cwd || "Choose a folder"}
                />
              </div>
              <DialogButton style={{ width: 120, minWidth: 0 }} onClick={browse}>
                Browse
              </DialogButton>
            </Focusable>
          </div>
        )}
        {messages.map((m, i) => (
          <MessageBubble key={i} message={m} />
        ))}
        {running && (
          <div style={{ display: "flex", alignItems: "center", gap: 8, opacity: 0.7, padding: "6px 0" }}>
            <Spinner style={{ width: 18 }} /> Claude is working…
          </div>
        )}
        <div ref={bottomRef} />
      </Focusable>

      <Focusable style={{ display: "flex", gap: 8, alignItems: "flex-end", padding: "10px 0 16px" }} flow-children="horizontal">
        <div style={{ flex: 1 }}>
          <TextField
            value={prompt}
            disabled={running || onRemote}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") send();
            }}
          />
        </div>
        {running ? (
          <DialogButton style={{ width: 120, minWidth: 0 }} onClick={() => job && stopJob(job.id)}>
            Stop
          </DialogButton>
        ) : (
          <DialogButton
            style={{ width: 120, minWidth: 0 }}
            disabled={!prompt.trim() || sending || onRemote || (!sessionId && !cwd)}
            onClick={send}
          >
            {sending ? "Sending…" : "Send"}
          </DialogButton>
        )}
      </Focusable>
    </Focusable>
  );
}
