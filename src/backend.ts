import { callable } from "@decky/api";

export interface Message {
  role: "user" | "assistant" | "tool";
  kind: "text" | "tool_use" | "tool_result";
  text: string;
  error?: boolean;
  ts?: string | null;
}

export interface Job {
  id: string;
  cwd: string;
  session_id: string | null;
  status: "running" | "done" | "error" | "stopped";
  messages: Message[];
  result: string | null;
  error: string | null;
  cost: number | null;
  started: number;
}

export interface SessionSummary {
  id: string;
  title: string;
  cwd: string;
  project: string;
  updated: number;
  turns: number;
  running_job?: string | null;
}

export interface Session extends Omit<SessionSummary, "running_job"> {
  messages: Message[];
  truncated: boolean;
  running_job: Job | null;
}

export interface Settings {
  claude_path: string;
  permission_mode: string;
  model: string;
  default_cwd: string;
  permission_modes: string[];
}

export interface Status {
  claude_path: string | null;
  version: string | null;
  projects_dir: string;
  has_projects: boolean;
  home: string;
}

export type JobEvent =
  | { job_id: string; type: "init"; session_id: string }
  | { job_id: string; type: "messages"; messages: Message[] }
  | { job_id: string; type: "end"; job: Job };

export const JOB_EVENT = "claude_job";

export const getStatus = callable<[], Status>("get_status");
export const getSettings = callable<[], Settings>("get_settings");
export const setSettings = callable<[settings: Partial<Settings>], Settings>("set_settings");
export const listSessions = callable<[limit: number], SessionSummary[]>("list_sessions");
export const getSession = callable<[sessionId: string, maxMessages: number], Session | null>("get_session");
export const recentFolders = callable<[], string[]>("recent_folders");
export const sendPrompt = callable<
  [sessionId: string, cwd: string, prompt: string],
  { job?: Job; error?: string }
>("send_prompt");
export const getJob = callable<[jobId: string], Job | null>("get_job");
export const stopJob = callable<[jobId: string], boolean>("stop_job");

export function timeAgo(epochSeconds: number): string {
  const s = Math.max(0, Date.now() / 1000 - epochSeconds);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
