export type SwitchProtocol = "openai-chat" | "openai-responses" | "anthropic";
export interface SwitchModel {
  id: string;
  name: string;
  contextWindow: number;
  maxTokens: number;
  vision: boolean;
}
export interface SwitchService {
  id: string;
  provider: "huaqing-switch";
  name: string;
  enabled: boolean;
  protocol: SwitchProtocol;
  baseURL: "managed-by-huaqing-switch";
  models: SwitchModel[];
  thinkingFormat?: "deepseek" | "qwen" | "zai";
}
export interface SwitchAlias {
  serviceId: string;
  modelId: string;
  connectionId: string;
  revision: number;
  serviceName: string;
  modelName: string;
  enabled: boolean;
  thinkingFormat?: "deepseek" | "qwen" | "zai";
}
export interface SwitchRoute {
  service: SwitchService;
  model: SwitchModel;
  protocol: SwitchProtocol;
  url: string;
  switchModelId: string;
  apiKey: string;
}
export function parseSwitchService(value: unknown): SwitchService;
export function switchProviderProfile(
  service: SwitchService,
  gateway: URL | string,
): {
  displayName: string;
  api: "openai-completions" | "openai-responses" | "anthropic-messages";
  apiKeyEnv: "DSH_TEAM_TOKEN";
  baseURL: string;
  models: {
    id: string;
    name: string;
    contextWindow: number;
    maxTokens: number;
    input: ("text" | "image")[];
  }[];
  compat?: {
    supportsStore: boolean;
    supportsDeveloperRole: boolean;
    maxTokensField: "max_tokens";
    supportsUsageInStreaming: boolean;
    thinkingFormat?: "deepseek" | "qwen" | "zai";
  };
};
export class SwitchCatalog {
  constructor(config: {
    endpoint: string;
    token: string;
    aliases?: { version: 1; aliases: SwitchAlias[] };
    timeoutMs?: number;
    ttlMs?: number;
    maxBytes?: number;
    fetcher?: typeof fetch;
  });
  close(): void;
  list(): Promise<SwitchService[]>;
  route(
    serviceId: string,
    modelId: string,
    protocol: SwitchProtocol,
  ): Promise<SwitchRoute | undefined>;
}
export function loadSwitchCatalog(
  environment?: NodeJS.ProcessEnv,
): Promise<SwitchCatalog | undefined>;
export function switchManagementUrl(environment?: NodeJS.ProcessEnv): string;
export function prepareSwitchInference(
  route: SwitchRoute,
  body: Record<string, unknown>,
): Record<string, unknown>;
export function switchInferenceHeaders(
  route: SwitchRoute,
  incoming?: Record<string, string | string[] | undefined>,
): Record<string, string>;
export function workerCapability(
  headers: Record<string, string | string[] | undefined>,
  allowApiKey: boolean,
): string | undefined;
