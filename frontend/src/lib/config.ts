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
