export interface GitHubRepository {
  github_id: number;
  owner: string;
  name: string;
  full_name: string;
  description: string | null;
  default_branch: string;
  is_private: boolean;
  language: string | null;
  updated_at: string | null;
  html_url: string;
  /** The local repository id when this one is already connected, else null. */
  connected_id: string | null;
}

export interface GitHubRepositoryPage {
  items: GitHubRepository[];
  page: number;
  per_page: number;
  /** GitHub reports no total for this endpoint, so only "is there more" is known. */
  has_next: boolean;
}

export type IndexStatus =
  | "not_indexed"
  | "queued"
  | "indexing"
  | "indexed"
  | "failed";

export interface ConnectedRepository {
  id: string;
  github_id: number;
  owner: string;
  name: string;
  default_branch: string;
  is_private: boolean;
  index_status: IndexStatus;
  indexed_at: string | null;
  created_at: string;
}
