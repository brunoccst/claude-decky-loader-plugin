import {
  ButtonItem,
  ConfirmModal,
  DropdownItem,
  Navigation,
  PanelSection,
  PanelSectionRow,
  showModal,
  staticClasses,
  TextField,
} from "@decky/ui";
import { definePlugin, routerHook, toaster } from "@decky/api";
import { useEffect, useState } from "react";
import { ClaudeIcon } from "./ClaudeIcon";
import {
  getSettings,
  getStatus,
  listProjects,
  listSessions,
  Project,
  SessionSummary,
  setSettings,
  Settings,
  Status,
  timeAgo,
} from "./backend";
import SessionPage, { NEW_SESSION, ROUTE, sessionPath, setNewSessionFolder } from "./SessionPage";
import { UpdateSection } from "./components/UpdateSection";
import { RemoteServerSection } from "./components/RemoteControl";
import { checkForUpdate, PLUGIN_NAME } from "./updater";
import { refreshPanelAfterReload } from "./reload";

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

function newSession(cwd = "") {
  setNewSessionFolder(cwd);
  open(sessionPath(NEW_SESSION));
}

function SessionRows({ sessions, showProject }: { sessions: SessionSummary[] | null; showProject: boolean }) {
  if (sessions === null) return <PanelSectionRow>Loading…</PanelSectionRow>;
  if (sessions.length === 0) {
    return (
      <PanelSectionRow>
        <div style={{ fontSize: 12, opacity: 0.7 }}>No Claude Code sessions here yet.</div>
      </PanelSectionRow>
    );
  }
  return (
    <>
      {sessions.map((s) => (
        <PanelSectionRow key={s.id}>
          <ButtonItem
            layout="below"
            description={[
              s.running_job ? "● working" : "",
              s.remote ? "📱 remote" : "",
              showProject ? s.project || s.cwd : "",
              timeAgo(s.updated),
            ]
              .filter(Boolean)
              .join(" · ")}
            onClick={() => open(sessionPath(s.id))}
          >
            <div style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", textAlign: "left" }}>
              {s.title}
            </div>
          </ButtonItem>
        </PanelSectionRow>
      ))}
    </>
  );
}

function SettingsModal({ closeModal, initial }: { closeModal?: () => void; initial: Settings }) {
  const [s, setS] = useState(initial);
  const save = async () => {
    const saved = await setSettings(s);
    toaster.toast({ title: "Claude Sessions", body: saved.error ?? "Settings saved" });
    closeModal?.();
  };
  return (
    // ConfirmModal, unlike ModalRoot, shows Save and Cancel buttons.
    <ConfirmModal
      closeModal={closeModal}
      onOK={save}
      onCancel={closeModal}
      strTitle="Claude Sessions settings"
      strOKButtonText="Save"
    >
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
        description="Empty uses your home folder. Created if it doesn't exist."
        value={s.default_cwd}
        onChange={(e) => setS({ ...s, default_cwd: e.target.value })}
      />
    </ConfirmModal>
  );
}

// Kept between panel openings so the panel reopens on the same project.
let lastProject: Project | null = null;

function Content() {
  const [status, setStatus] = useState<Status | null>(null);
  const [project, setProjectState] = useState<Project | null>(lastProject);
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null);

  const setProject = (p: Project | null) => {
    lastProject = p;
    setProjectState(p);
  };

  const refresh = async () => {
    setSessions(null);
    if (project) {
      setSessions(await listSessions(100, project.cwd));
    } else {
      const [p, s] = await Promise.all([listProjects(), listSessions(10)]);
      setProjects(p);
      setSessions(s);
    }
  };

  useEffect(() => {
    getStatus().then(setStatus);
  }, []);
  useEffect(() => {
    refresh();
  }, [project?.cwd]);

  const noCli = !status?.claude_path;

  return (
    <>
      {status && noCli && (
        <PanelSection>
          <PanelSectionRow>
            <div style={{ fontSize: 12, color: "#ff8a80" }}>
              Claude Code CLI not found. Install it on this device (curl -fsSL https://claude.ai/install.sh | bash)
              and sign in once from desktop mode, or set its path in settings.
            </div>
          </PanelSectionRow>
        </PanelSection>
      )}

      {project ? (
        <PanelSection title={project.name}>
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={() => setProject(null)}>
              ← All folders
            </ButtonItem>
          </PanelSectionRow>
          <PanelSectionRow>
            <div style={{ fontSize: 11, opacity: 0.6, wordBreak: "break-all" }}>{project.cwd}</div>
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              onClick={() => newSession(project.cwd)}
              disabled={noCli || !project.exists}
              description={project.exists ? undefined : "This folder doesn't exist anymore."}
            >
              New session here
            </ButtonItem>
          </PanelSectionRow>
          <SessionRows sessions={sessions} showProject={false} />
        </PanelSection>
      ) : (
        <>
          <PanelSection>
            <PanelSectionRow>
              <ButtonItem layout="below" onClick={() => newSession()} disabled={noCli}>
                New session
              </ButtonItem>
            </PanelSectionRow>
          </PanelSection>

          <PanelSection title="Folders">
            {projects === null && <PanelSectionRow>Loading…</PanelSectionRow>}
            {projects?.length === 0 && (
              <PanelSectionRow>
                <div style={{ fontSize: 12, opacity: 0.7 }}>No Claude Code sessions on this device yet.</div>
              </PanelSectionRow>
            )}
            {projects?.map((p) => (
              <PanelSectionRow key={p.cwd}>
                <ButtonItem
                  layout="below"
                  description={`${p.cwd} · ${p.sessions} session${p.sessions === 1 ? "" : "s"} · ${timeAgo(p.updated)}`}
                  onClick={() => setProject(p)}
                >
                  <div style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", textAlign: "left" }}>
                    {p.name}
                  </div>
                </ButtonItem>
              </PanelSectionRow>
            ))}
          </PanelSection>

          <PanelSection title="Recent sessions">
            <SessionRows sessions={sessions} showProject={true} />
          </PanelSection>
        </>
      )}

      <PanelSection>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={refresh}>
            Refresh
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>

      <RemoteServerSection disabled={noCli} />

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
        {status?.service === "plugin" && (
          <PanelSectionRow>
            <div style={{ fontSize: 11, opacity: 0.7 }}>
              Running inside Decky, so Remote Control stops if Decky does.
              {status.service_error ? ` ${status.service_error}` : ""}
            </div>
          </PanelSectionRow>
        )}
      </PanelSection>
      <UpdateSection />
    </>
  );
}

export default definePlugin(() => {
  routerHook.addRoute(ROUTE, SessionPage, { exact: true });
  refreshPanelAfterReload(PLUGIN_NAME);

  // Let the user know about a new release once per boot; the panel has the button.
  checkForUpdate()
    .then((update) => {
      if (update) {
        toaster.toast({
          title: "Claude Sessions",
          body: `Version ${update.latest} is available. Update it from the Claude Sessions panel.`,
        });
      }
    })
    .catch(() => {});

  return {
    name: "Claude Sessions",
    titleView: <div className={staticClasses.Title}>Claude Sessions</div>,
    content: <Content />,
    icon: <ClaudeIcon />,
    onDismount() {
      routerHook.removeRoute(ROUTE);
    },
  };
});
