import { invoke } from "@tauri-apps/api/core";

export type HqProtocol = "openai-responses" | "openai-chat" | "anthropic";
export interface HqConnection {
  id: string;
  name: string;
  protocol: HqProtocol;
  baseUrl: string;
  model: string;
  apiKeyConfigured: boolean;
  contextWindow: number;
  maxOutputTokens: number;
  supportsImages: boolean;
}
export interface HqSnapshot {
  revision: number;
  activeId: string | null;
  connections: HqConnection[];
}
export interface HqConnectionInput {
  id?: string;
  name: string;
  protocol: HqProtocol;
  baseUrl: string;
  model: string;
  apiKey?: string;
  contextWindow: number;
  maxOutputTokens: number;
  supportsImages: boolean;
}

export interface HqGatewayStatus {
  running: boolean;
  address: string;
  port: number;
  error: string | null;
  activeRequests: number;
  totalRequests: number;
}

export interface HqUsageFilter {
  /** Local calendar ranges are sent as epoch seconds; the end is exclusive. */
  startTime?: number;
  endTime?: number;
  connectionId?: string;
  model?: string;
  offset?: number;
  limit?: number;
}

export interface HqTokenUsage {
  /** Input includes cache reads/writes; cache fields are subsets, not extra input. */
  inputTokens: number | null;
  outputTokens: number | null;
  cacheReadTokens: number | null;
  cacheWriteTokens: number | null;
  totalTokens: number | null;
}

export interface HqUsageSummary extends HqTokenUsage {
  requests: number;
  successful: number;
  failed: number;
  cancelled: number;
  interrupted: number;
  usageReported: number;
  usageMissing: number;
  usagePartial: number;
  usageFinal: number;
  durationMs: number;
  avgDurationMs: number | null;
}

export interface HqUsageGroup {
  connectionId: string;
  connectionName: string;
  model: string;
  protocol: HqProtocol;
  summary: HqUsageSummary;
}

export type HqRequestOutcome =
  | "success"
  | "failed"
  | "cancelled"
  | "interrupted";

export interface HqUsageEntry extends HqTokenUsage {
  id: string;
  startedAt: number;
  connectionId: string;
  connectionName: string;
  connectionRevision: number;
  model: string;
  protocol: HqProtocol;
  streamed: boolean;
  outcome: HqRequestOutcome;
  httpStatus: number | null;
  failureCode: string | null;
  durationMs: number | null;
  usageReported: boolean;
  usageStatus: "unknown" | "partial" | "final";
}

export interface HqUsageSnapshot {
  summary: HqUsageSummary;
  perConnectionModel: HqUsageGroup[];
  recent: HqUsageEntry[];
  total: number;
  inFlight: number;
  storageError: boolean;
  /** All HQ history options, including connections that have since been deleted. */
  connections: { id: string; name: string }[];
  models: string[];
}

export const hqApi = {
  status: () => invoke<HqGatewayStatus>("hq_get_gateway_status"),
  retryGateway: () => invoke<HqGatewayStatus>("hq_retry_gateway"),
  get: () => invoke<HqSnapshot>("hq_get_connections"),
  save: (revision: number, connection: HqConnectionInput) =>
    invoke<HqSnapshot>("hq_save_connection", { revision, connection }),
  select: (revision: number, id: string) =>
    invoke<HqSnapshot>("hq_select_connection", { revision, id }),
  remove: (revision: number, id: string) =>
    invoke<HqSnapshot>("hq_delete_connection", { revision, id }),
  usage: (filter: HqUsageFilter = {}) =>
    invoke<HqUsageSnapshot>("hq_get_usage", { filter }),
};
