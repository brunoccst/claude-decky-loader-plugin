import { ButtonItem, PanelSection, PanelSectionRow } from "@decky/ui";
import { useEffect, useState } from "react";
import { checkForUpdate, getVersion, installUpdate, UpdateInfo } from "../updater";

type Status =
  | { kind: "idle" }
  | { kind: "checking" }
  | { kind: "upToDate" }
  | { kind: "available"; update: UpdateInfo }
  | { kind: "error"; message: string };

export function UpdateSection() {
  const [version, setVersion] = useState("");
  const [status, setStatus] = useState<Status>({ kind: "idle" });

  useEffect(() => {
    getVersion().then(setVersion).catch(() => {});
  }, []);

  const check = async () => {
    setStatus({ kind: "checking" });
    try {
      const update = await checkForUpdate();
      setStatus(update ? { kind: "available", update } : { kind: "upToDate" });
    } catch (e) {
      setStatus({ kind: "error", message: String(e) });
    }
  };

  const install = async (update: UpdateInfo) => {
    try {
      await installUpdate(update);
    } catch (e) {
      setStatus({ kind: "error", message: String(e) });
    }
  };

  return (
    <PanelSection title={version ? `Version ${version}` : "Updates"}>
      {status.kind === "available" ? (
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => install(status.update)}>
            Update to {status.update.latest}
          </ButtonItem>
        </PanelSectionRow>
      ) : (
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={status.kind === "checking"} onClick={check}>
            {status.kind === "checking" ? "Checking…" : "Check for updates"}
          </ButtonItem>
        </PanelSectionRow>
      )}
      {status.kind === "upToDate" && (
        <PanelSectionRow>
          <div>You're on the latest version.</div>
        </PanelSectionRow>
      )}
      {status.kind === "error" && (
        <PanelSectionRow>
          <div>Couldn't check for updates: {status.message}</div>
        </PanelSectionRow>
      )}
    </PanelSection>
  );
}
