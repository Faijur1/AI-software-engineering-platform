# ADR-010 — Fetch repository snapshots as tarballs, not with `git clone`

- **Status:** Accepted
- **Date:** 2026-09-01

## Context

Ingestion needs the file tree of a repository at a known commit. The obvious
approach is `git clone --depth 1`, which is what the ingestion flow in
[`docs/hld.md`](../hld.md) describes generically as "clone/fetch".

Cloning a *private* repository over HTTPS requires presenting the OAuth token,
and every way of doing that with the git CLI puts the credential somewhere it
does not belong:

- in the URL (`https://x-access-token:TOKEN@github.com/...`), which lands in
  the process argument list, in `~/.git-credentials` if anything caches it, and
  in git's own error output;
- in `-c http.extraHeader=...`, which is equally visible in `ps` and `/proc`;
- in a credential helper, which means writing the token to disk.

Anything on the machine that can list processes can read the first two. That is
a poor trade for a capability -- git history -- that ingestion never uses.

## Decision

Fetch `GET /repos/{owner}/{repo}/tarball/{sha}` with an `Authorization` header
and extract it to a temporary directory that is deleted when the run ends.

The commit SHA is resolved first through `GET /repos/{owner}/{repo}/commits/
{ref}`, so every indexed file records the exact revision it came from.

## Alternatives considered

- **`git clone --depth 1`.** Rejected for the credential exposure above, and
  because it adds a dependency on a `git` binary being installed and on PATH in
  whatever container the worker eventually runs in.
- **A git library (dulwich, pygit2).** Avoids the argv problem and keeps real
  git semantics, but adds a substantial dependency -- pygit2 needs libgit2
  built for the platform -- to do strictly more than is needed.
- **Fetching files individually through the contents API.** One HTTP request
  per file. Rate limits make this unusable for a repository of any size.

## Consequences

**Positive**

- The token appears only in a request header, never in a process argument, a
  config file, or on disk.
- No `git` binary dependency.
- Extraction is a single, auditable place to enforce the safety rules that
  untrusted archives demand: `filter="data"` rejects absolute paths, `..`
  traversal, symlinks pointing outside the tree and device nodes, and explicit
  ceilings bound both the download and the expanded size.
- The temporary directory is removed on every exit path, so a failed index
  leaves nothing behind.

**Negative**

- **A re-index re-downloads the entire tree** rather than fetching a delta. At
  Stage 1 repository sizes this is seconds, and the expensive part is avoided
  anyway: per-file `content_hash` comparison means unchanged files are not
  re-parsed, and from milestone 4 not re-embedded.
- No access to history, blame, or diffs between commits. Nothing in Stage 1
  needs them.
- GitHub-specific. Supporting another host means another fetcher, though the
  rest of ingestion is unaffected because it only ever sees a local directory.

**Revisit if** a feature needs git history — blame-based context, or diffing
two commits for incremental indexing — or if repository sizes make full
re-download the dominant cost. Because ingestion consumes a plain directory,
swapping the fetcher is a contained change.
