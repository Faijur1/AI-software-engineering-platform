export type DependencyStatus = "ok" | "unavailable";

export interface DependencyHealth {
  status: DependencyStatus;
  latency_ms: number | null;
  error: string | null;
}

export interface HealthResponse {
  status: "ok" | "degraded";
  environment: string;
  dependencies: Record<string, DependencyHealth>;
}
