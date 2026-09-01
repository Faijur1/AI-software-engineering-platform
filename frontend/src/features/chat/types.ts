export interface CitedSource {
  index: number;
  chunk_id: string;
  file_path: string;
  symbol: string | null;
  start_line: number;
  end_line: number;
  content: string;
  /** Whether the answer actually referred to this source. */
  cited: boolean;
}

export interface CitationCheck {
  /** False when the answer cited a source that does not exist. */
  valid: boolean;
  invalid_indices: number[];
  /** Share of sentences carrying a citation. Coverage, not correctness. */
  citation_coverage: number;
}

export interface ChatResponse {
  question: string;
  repository_id: string;
  answer: string;
  sources: CitedSource[];
  citations: CitationCheck;
  model: string;
  reranker: string;
  retrieved_candidates: number;
  sources_offered: number;
  sources_included: number;
  estimated_context_tokens: number;
  duration_ms: number;
  notes: string[];
}
