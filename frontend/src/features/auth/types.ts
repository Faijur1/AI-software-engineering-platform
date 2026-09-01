export interface User {
  id: string;
  github_id: number;
  login: string;
  name: string | null;
  email: string | null;
  avatar_url: string | null;
}
