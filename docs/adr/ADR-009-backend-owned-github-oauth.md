# ADR-009 — The backend owns the GitHub OAuth flow

- **Status:** Accepted
- **Date:** 2026-09-01
- **Supersedes:** the "NextAuth JWT, verified backend-side" line in
  [`docs/security.md`](../security.md)

## Context

Milestone 2 signs users in with GitHub and lists their repositories. The
original design had NextAuth run the OAuth flow in the Next.js frontend, with
the backend verifying the resulting session token.

Two facts made that awkward:

1. **The backend is what needs the GitHub access token.** It lists repositories
   now, and from milestone 3 it clones and indexes them from a background
   worker with no browser in the loop. Under the NextAuth design the frontend
   obtains the token and forwards it to the backend to store — so the token
   crosses an extra process and an extra network hop, and the frontend becomes
   a credential custodian for no benefit.
2. **NextAuth's session cookie is encrypted, not merely signed.** It is a JWE
   (HKDF-derived key, `A256CBC-HS512`), not a plain JWS. Verifying it in Python
   means reimplementing that derivation and tracking it across NextAuth
   releases — a fragile dependency on another framework's internal format,
   sitting directly on the authentication path.

`TOKEN_ENCRYPTION_KEY` was already in `.env.example` for encrypting GitHub
tokens at rest, so the design already assumed the backend stores them.

## Decision

The FastAPI backend owns the entire OAuth round trip.

- `GET /auth/github/login` redirects to GitHub and pins an unguessable `state`
  in a short-lived HttpOnly cookie.
- `GET /auth/github/callback` compares that state in constant time, exchanges
  the code for an access token, upserts the user, encrypts the token, and sets
  a signed session cookie (HS256, `SESSION_SECRET`).
- The frontend holds no credentials. It reads the session server-side and
  forwards the cookie from server components and server actions.

The registered GitHub callback URL is therefore
`http://localhost:8000/auth/github/callback`, not the NextAuth path.

`SESSION_SECRET` is also read from `NEXTAUTH_SECRET` so existing `.env` files
keep working.

## Alternatives considered

- **NextAuth with JWE decryption in Python.** Rejected: reimplements another
  framework's internal cookie format on the authentication path.
- **NextAuth switched to emit a plain JWS.** Workable, but still leaves the
  frontend holding the GitHub token and forwarding it to the backend.
- **Backend-issued opaque session IDs in Redis.** Stronger — revocation becomes
  immediate — but adds a Redis round trip per request and a second store of
  record. A signed cookie plus a per-request database load of the user gives
  most of the benefit; see Consequences.

## Consequences

**Positive**

- The GitHub token is written by, and readable only by, the component that
  uses it. It never reaches the browser and never touches the frontend process.
- The authentication path depends on `PyJWT` and `cryptography` rather than on
  another framework's cookie internals.
- The whole flow is testable end to end from the backend test suite, with
  GitHub mocked at the HTTP layer — including the CSRF-state rejection paths.
- The frontend keeps no auth dependency at all.

**Negative**

- Sign-out cannot revoke an already-issued cookie before it expires. Mitigated
  by loading the user from the database on every request, so a deleted account
  stops working immediately; full revocation needs the Redis session store
  above.
- The session cookie is set on the backend's origin. Local development works
  because `localhost:3000` and `localhost:8000` are the same site — ports are
  not part of a cookie's origin. **A production deployment that splits the
  frontend and backend across different registrable domains will need either a
  shared parent domain or a Next.js proxy route.** This is recorded here rather
  than solved now because Stage 1 is local-only (see
  [`docs/deployment.md`](../deployment.md)).
- Two secrets to manage instead of one.

**Revisit if** session revocation becomes a requirement, or when Stage 3
deployment settles the frontend/backend domain layout.
