import {
  ButtonItem,
  DropdownItem,
  ModalRoot,
  Navigation,
  PanelSection,
  PanelSectionRow,
  showModal,
  staticClasses,
  TextField,
} from "@decky/ui";
import { definePlugin, routerHook, toaster } from "@decky/api";
import { useEffect, useState } from "react";
import { FaTerminal } from "react-icons/fa";
import {
  getSettings,
  getStatus,
  listSessions,
  SessionSummary,
  setSettings,
  Settings,
  Status,
  timeAgo,
} from "./backend";
import SessionPage, { NEW_SESSION, ROUTE, sessionPath } from "./SessionPage";

const PERMISSION_LABELS: Record<string, string> = {
  "": "Claude Code default",
  acceptEdits: "Accept edits",
  plan: "Plan only",
  auto: "Auto",
  bypassPermissions: "Bypass all permissions",
};

function open(path: string) {
  Navigation.Navigate(path);
  Navigation.CloseSideMenus();
}

function SettingsModal({ closeModal, initial }: { closeModal?: () => void; initial: Settings }) {
  const [s, setS] = useState(initial);
  const save = async () => {
    await setSettings(s);
    toaster.toast({ title: "Claude Sessions", body: "Settings saved" });
    closeModal?.();
  };
  return (
    <ModalRoot closeModal={closeModal} onOK={save} onCancel={closeModal}>
      <div className={staticClasses.Title} style={{ marginBottom: 12 }}>Claude Sessions settings</div>
      <TextField
        label="Claude Code CLI path"
        description="Leave empty to find it automatically (~/.local/bin/claude, npm global, …)."
        value={s.claude_path}
        onChange={(e) => setS({ ...s, claude_path: e.target.value })}
      />
      <DropdownItem
        label="Permission mode"
        description="Prompts run unattended, so tools that need approval are refused unless a mode allows them."
        rgOptions={s.permission_modes.map((m) => ({ data: m, label: PERMISSION_LABELS[m] ?? m }))}
        selectedOption={s.permission_mode}
        onChange={(o) => setS({ ...s, permission_mode: o.data })}
      />
      <TextField
        label="Model"
        description="Optional, e.g. opus or sonnet. Empty uses your Claude Code default."
        value={s.model}
        onChange={(e) => setS({ ...s, model: e.target.value })}
      />
      <TextField
        label="Default folder for new sessions"
        description="Empty uses your home folder."
        value={s.default_cwd}
        onChange={(e) => setS({ ...s, default_cwd: e.target.value })}
      />
    </ModalRoot>
  );
}

function Content() {
  const [status, setStatus] = useState<Status | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null);

  const refresh = async () => {
    setSessions(await listSessions(30));
  };

  useEffect(() => {
    getStatus().then(setStatus);
    refresh();
  }, []);

  return (
    <>
      <PanelSection>
        {status && !status.claude_path && (
          <PanelSectionRow>
            <div style={{ fontSize: 12, color: "#ff8a80" }}>
              Claude Code CLI not found. Install it on this device (curl -fsSL https://claude.ai/install.sh | bash)
              and sign in once from desktop mode, or set its path in settings.
            </div>
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => open(sessionPath(NEW_SESSION))} disabled={!status?.claude_path}>
            New session
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Sessions">
        {sessions === null && <PanelSectionRow>Loading…</PanelSectionRow>}
        {sessions?.length === 0 && (
          <PanelSectionRow>
            <div style={{ fontSize: 12, opacity: 0.7 }}>No Claude Code sessions on this device yet.</div>
          </PanelSectionRow>
        )}
        {sessions?.map((s) => (
          <PanelSectionRow key={s.id}>
            <ButtonItem
              layout="below"
              description={`${s.running_job ? "● working · " : ""}${s.project || s.cwd} · ${timeAgo(s.updated)}`}
              onClick={() => open(sessionPath(s.id))}
            >
              <div style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", textAlign: "left" }}>
                {s.title}
              </div>
            </ButtonItem>
          </PanelSectionRow>
        ))}
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={refresh}>
            Refresh
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={async () => {
              const initial = await getSettings();
              showModal(<SettingsModal initial={initial} />);
            }}
          >
            Settings
          </ButtonItem>
        </PanelSectionRow>
        {status?.version && (
          <PanelSectionRow>
            <div style={{ fontSize: 11, opacity: 0.5 }}>{status.version}</div>
          </PanelSectionRow>
        )}
      </PanelSection>
    </>
  );
}

export default definePlugin(() => {
  routerHook.addRoute(ROUTE, SessionPage, { exact: true });

  return {
    name: "Claude Sessions",
    titleView: <div className={staticClasses.Title}>Claude Sessions</div>,
    content: <Content />,
    icon: <FaTerminal />,
    onDismount() {
      routerHook.removeRoute(ROUTE);
    },
  };
});
