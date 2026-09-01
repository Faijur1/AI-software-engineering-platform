/**
 * Frontend configuration, read from the environment in one place.
 *
 * Server-side code uses BACKEND_URL; anything that must reach the browser uses
 * the NEXT_PUBLIC_ variant. Nothing secret belongs in a NEXT_PUBLIC_ value.
 */

export const backendUrl =
  process.env.BACKEND_URL ??
  process.env.NEXT_PUBLIC_BACKEND_URL ??
  "http://localhost:8000";

/**
 * The backend URL as the *browser* must see it.
 *
 * Used only for links the browser navigates to directly — the OAuth sign-in
 * redirect. It may differ from `backendUrl`, which can be an internal address
 * the browser cannot resolve.
 */
export const publicBackendUrl =
  process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";
