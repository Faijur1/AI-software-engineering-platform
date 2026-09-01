export type JobStatus = "queued" | "running" | "succeeded" | "failed";

export interface Job {
  id: string;
  type: "index_repository";
  status: JobStatus;
  repository_id: string;
  /** 0-100. Coarse by design: a stage boundary, not an estimate. */
  progress: number;
  stage: string | null;
  started_at: string | null;
  finished_at: string | null;
  /** Only ever a message the backend judged safe to show. */
  error: string | null;
  created_at: string;
}
