export type RetrievalMethod = "vector" | "keyword" | "both";

export interface SearchHit {
  chunk_id: string;
  file_path: string;
  symbol: string | null;
  kind: string;
  start_line: number;
  end_line: number;
  content: string;
  method: RetrievalMethod;
  fused_score: number;
  vector_score: number | null;
  vector_rank: number | null;
  keyword_score: number | null;
  keyword_rank: number | null;
  /** Null means "not reranked" — never "reranked and unchanged". */
  rerank_score: number | null;
}

export interface SearchResponse {
  query: string;
  repository_id: string;
  results: SearchHit[];
  vector_candidates: number;
  keyword_candidates: number;
  fused_candidates: number;
  notes: string[];
  reranker: string;
  reranker_is_passthrough: boolean;
}
