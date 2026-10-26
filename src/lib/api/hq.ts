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
};
