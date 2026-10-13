import { DialogButton, Focusable, PanelSection, PanelSectionRow, ToggleField } from "@decky/ui";
import { addEventListener, removeEventListener, toaster } from "@decky/api";
import { useEffect, useState } from "react";
import {
  answerRemote,
  getRemote,
  Remote,
  REMOTE_EVENT,
  REMOTE_SERVER,
  startRemote,
  stopRemote,
} from "../backend";

/** Live state of one Remote Control process, or null when it isn't running. */
export function useRemote(key: string) {
  const [remote, setRemote] = useState<Remote | null>(null);

  useEffect(() => {
    if (!key) return;
    getRemote(key).then(setRemote);
    const listener = addEventListener<[Remote]>(REMOTE_EVENT, (r) => {
      if (r.key === key) setRemote(r);
    });
    return () => removeEventListener(REMOTE_EVENT, listener);
  }, [key]);

  const active = !!remote && remote.status !== "exited";

  const start = async () => {
    const res = await startRemote(key);
    if (res.error || !res.remote) {
      toaster.toast({ title: "Remote Control", body: res.error ?? "Couldn't start Remote Control." });
      return;
    }
    setRemote(res.remote);
  };
  const stop = async () => {
    await stopRemote(key);
    setRemote((r) => r && { ...r, status: "exited", question: null });
  };
  const answer = (text: string) => answerRemote(key, text);

  return { remote, active, start, stop, answer };
}

function statusText(remote: Remote | null): string {
  if (!remote) return "";
  switch (remote.status) {
    case "starting":
      return "Connecting…";
    case "running":
      return "Connected. Open it in the Claude app or at claude.ai/code.";
    case "question":
      return "Claude Code is asking:";
    case "exited":
      return remote.error ? "Stopped with an error:" : "Stopped.";
  }
}

/** Status, the CLI's question with Yes/No, the session link and any error. */
function RemoteDetails({ remote, answer }: { remote: Remote; answer: (text: string) => void }) {
  return (
    <div style={{ fontSize: 12, lineHeight: 1.4 }}>
      <div style={{ opacity: 0.8 }}>{statusText(remote)}</div>
      {remote.question && <div style={{ margin: "4px 0", fontWeight: "bold" }}>{remote.question}</div>}
      {remote.question && (
        <Focusable style={{ display: "flex", gap: 8, marginTop: 4 }} flow-children="horizontal">
          <DialogButton style={{ minWidth: 0, flex: 1 }} onClick={() => answer("y\r")}>
            Yes
          </DialogButton>
          <DialogButton style={{ minWidth: 0, flex: 1 }} onClick={() => answer("n\r")}>
            No
          </DialogButton>
        </Focusable>
      )}
      {remote.url && remote.status !== "exited" && (
        <div style={{ marginTop: 4, wordBreak: "break-all", opacity: 0.7 }}>{remote.url}</div>
      )}
      {remote.status === "starting" && !remote.question && remote.output && (
        <pre style={{ marginTop: 4, fontSize: 10, opacity: 0.6, whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
          {remote.output}
        </pre>
      )}
      {remote.status === "exited" && remote.error && (
        <pre style={{ marginTop: 4, fontSize: 10, color: "#ff8a80", whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
          {remote.error}
        </pre>
      )}
    </div>
  );
}

/** Quick Access panel section for the Remote Control server. */
export function RemoteServerSection({ disabled }: { disabled: boolean }) {
  const { remote, active, start, stop, answer } = useRemote(REMOTE_SERVER);
  return (
    <PanelSection title="Remote Control">
      <PanelSectionRow>
        <ToggleField
          label="Start sessions from your phone"
          description="Runs Claude Code's Remote Control server in your default folder. It keeps running in Game Mode and after a restart."
          checked={active}
          disabled={disabled}
          onChange={(on) => (on ? start() : stop())}
        />
      </PanelSectionRow>
      {remote && (active || remote.error) && (
        <PanelSectionRow>
          <RemoteDetails remote={remote} answer={answer} />
        </PanelSectionRow>
      )}
    </PanelSection>
  );
}

/** Button and status for putting one session on Remote Control. */
export function SessionRemoteControl({ sessionId, busy }: { sessionId: string; busy: boolean }) {
  const { remote, active, start, stop, answer } = useRemote(sessionId);
  return (
    <div style={{ padding: "0 0 8px" }}>
      <Focusable style={{ display: "flex", gap: 8, alignItems: "center" }} flow-children="horizontal">
        <DialogButton
          style={{ width: 260, minWidth: 0, padding: "6px 12px" }}
          disabled={!active && busy}
          onClick={active ? stop : start}
        >
          {active ? "Stop Remote Control" : "Continue on phone"}
        </DialogButton>
        {!active && !remote?.error && (
          <div style={{ fontSize: 12, opacity: 0.6 }}>Opens this session in the Claude app with Remote Control.</div>
        )}
      </Focusable>
      {remote && (active || remote.error) && (
        <div style={{ marginTop: 6 }}>
          <RemoteDetails remote={remote} answer={answer} />
        </div>
      )}
    </div>
  );
}
