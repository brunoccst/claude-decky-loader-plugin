/**
 * Decky keeps the plugin that's open in the Quick Access Menu as a stored
 * object. When the plugin is updated or reloaded, Decky swaps in the new
 * plugin but the open panel keeps rendering the old one until it's closed.
 * Re-selecting the plugin once the new one is registered makes the open panel
 * show the new version right away.
 */

interface DeckyStateLike {
  publicState(): { plugins: { name: string }[]; activePlugin: { name: string } | null };
  setActivePlugin(name: string): void;
}

function deckyState(): DeckyStateLike | undefined {
  // Not part of Decky's public API; guarded so a change there only skips the refresh.
  return (window as any).DeckyPluginLoader?.deckyState;
}

export function refreshPanelAfterReload(name: string, attempts = 20, delayMs = 250) {
  let tries = 0;
  const tick = () => {
    try {
      const state = deckyState();
      if (!state) return;
      const { plugins, activePlugin } = state.publicState();
      if (!activePlugin || activePlugin.name !== name) return;
      const current = plugins.find((p) => p.name === name);
      if (current && current !== activePlugin) {
        state.setActivePlugin(name);
        return;
      }
    } catch {
      return;
    }
    // Decky registers the new plugin just after this module runs; try again shortly.
    if (++tries < attempts) setTimeout(tick, delayMs);
  };
  setTimeout(tick, 0);
}
