import { callable, fetchNoCors } from "@decky/api";

// Updates come from this repo's GitHub releases and are installed through
// Decky's own installer, which shows its usual confirmation prompt.
const RELEASES_URL =
  "https://api.github.com/repos/brunoccst/claude-decky-loader-plugin/releases?per_page=10";
const ASSET_NAME = "claude-sessions.zip";
const PLUGIN_NAME = "Claude Sessions"; // must match plugin.json
const INSTALL_TYPE_UPDATE = 2;

export const getVersion = callable<[], string>("get_version");

export interface UpdateInfo {
  current: string;
  latest: string;
  url: string;
  hash: string;
}

interface ParsedVersion {
  core: number[];
  pre: (string | number)[];
}

const parse = (v: string): ParsedVersion => {
  const [core, pre] = v.replace(/^v/, "").split("-", 2);
  return {
    core: core.split(".").map((n) => parseInt(n, 10) || 0),
    pre: pre ? pre.split(".").map((p) => (/^\d+$/.test(p) ? parseInt(p, 10) : p)) : [],
  };
};

// Semver ordering: 1.0.0-alpha.2 < 1.0.0-alpha.10 < 1.0.0.
export function compareVersions(a: string, b: string): number {
  const x = parse(a);
  const y = parse(b);
  for (let i = 0; i < 3; i++) {
    const d = (x.core[i] ?? 0) - (y.core[i] ?? 0);
    if (d) return Math.sign(d);
  }
  if (!x.pre.length || !y.pre.length) return Math.sign(y.pre.length - x.pre.length);
  for (let i = 0; i < Math.max(x.pre.length, y.pre.length); i++) {
    const p = x.pre[i];
    const q = y.pre[i];
    if (p === undefined) return -1;
    if (q === undefined) return 1;
    if (p === q) continue;
    if (typeof p === "number" && typeof q === "number") return Math.sign(p - q);
    if (typeof p === "number") return -1;
    if (typeof q === "number") return 1;
    return p < q ? -1 : 1;
  }
  return 0;
}

// Returns the newest release if it's newer than what's installed, otherwise null.
export async function checkForUpdate(): Promise<UpdateInfo | null> {
  const current = await getVersion();
  const res = await fetchNoCors(RELEASES_URL, {
    headers: { Accept: "application/vnd.github+json" },
  });
  if (!res.ok) {
    throw new Error(
      res.status === 404
        ? "GitHub returned 404. The repository has to be public for updates."
        : `GitHub returned ${res.status}`,
    );
  }

  const releases: any[] = await res.json();
  let newest: UpdateInfo | null = null;
  for (const release of releases) {
    if (release.draft) continue;
    const asset = release.assets?.find((a: any) => a.name === ASSET_NAME);
    if (!asset) continue;
    const latest = String(release.tag_name).replace(/^v/, "");
    if (!newest || compareVersions(latest, newest.latest) > 0) {
      newest = {
        current,
        latest,
        url: asset.browser_download_url,
        hash: String(asset.digest ?? "").replace(/^sha256:/, ""),
      };
    }
  }
  return newest && compareVersions(newest.latest, current) > 0 ? newest : null;
}

export function installUpdate(update: UpdateInfo) {
  const backend = (window as any).DeckyBackend;
  if (!backend?.call) throw new Error("Decky's installer is unavailable");
  return backend.call(
    "utilities/install_plugin",
    update.url,
    PLUGIN_NAME,
    update.latest,
    update.hash,
    INSTALL_TYPE_UPDATE,
  );
}
