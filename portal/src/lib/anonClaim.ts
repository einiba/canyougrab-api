import { API_BASE } from "@/config";

/**
 * Anonymous-list claim flow:
 * 1. The marketing site appends `?vid=<visitorId>` to portal links.
 * 2. captureVidFromUrl() pulls it off the URL on first paint and stashes it
 *    in localStorage so it survives the Auth0 redirect round trip.
 * 3. claimPending() runs after the post-login session call and POSTs the vid
 *    to /api/names/claim, then clears the stash.
 *
 * Server-side is idempotent — re-claiming returns 0 with no harm.
 */

const STASH_KEY = "cygi_pending_vid";
const VID_PARAM = "vid";

export function captureVidFromUrl(): void {
  if (typeof window === "undefined") return;
  try {
    const params = new URLSearchParams(window.location.search);
    const vid = params.get(VID_PARAM);
    if (!vid) return;
    localStorage.setItem(STASH_KEY, vid);
    params.delete(VID_PARAM);
    const qs = params.toString();
    const newUrl = window.location.pathname + (qs ? `?${qs}` : "") + window.location.hash;
    window.history.replaceState({}, "", newUrl);
  } catch {
    /* ignore — non-critical */
  }
}

export async function claimPending(
  signRequest: (req: Request) => Promise<Request>,
): Promise<number> {
  if (typeof window === "undefined") return 0;
  let vid = "";
  try {
    vid = localStorage.getItem(STASH_KEY) ?? "";
  } catch {
    return 0;
  }
  if (!vid) return 0;

  try {
    const req = await signRequest(
      new Request(`${API_BASE}/api/names/claim`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ visitor_id: vid }),
      }),
    );
    const res = await fetch(req);
    if (res.ok) {
      try { localStorage.removeItem(STASH_KEY); } catch { /* ignore */ }
      const data = await res.json().catch(() => ({}));
      return Number(data?.claimed ?? 0);
    }
    return 0;
  } catch {
    return 0;
  }
}
