import FingerprintJS from "@fingerprintjs/fingerprintjs";
import { API_BASE_URL } from "@/config";
import { logger } from "@/lib/logger";

const VISITOR_ID_KEY = "cygi_visitor_id";

export function getVisitorId(): string {
  try {
    let id = localStorage.getItem(VISITOR_ID_KEY);
    if (!id) {
      id = generateUuid();
      localStorage.setItem(VISITOR_ID_KEY, id);
    }
    return id;
  } catch {
    return generateUuid();
  }
}

let fpPromise: Promise<string> | null = null;

export function getFingerprint(): Promise<string> {
  if (!fpPromise) {
    fpPromise = FingerprintJS.load()
      .then((fp) => fp.get())
      .then((res) => res.visitorId)
      .catch((err) => {
        logger.warn("Fingerprint load failed", { error: String(err) });
        return "";
      });
  }
  return fpPromise;
}

export async function getVisitorHeaders(): Promise<Record<string, string>> {
  const visitorId = getVisitorId();
  const fingerprint = await getFingerprint();
  const headers: Record<string, string> = { "X-Visitor-Id": visitorId };
  if (fingerprint) headers["X-Visitor-Fingerprint"] = fingerprint;
  return headers;
}

function generateUuid(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return "v-" + Math.random().toString(36).slice(2) + Date.now().toString(36);
}

/**
 * Returns the given URL with `?vid=<visitorId>` appended, so the portal can
 * pick it up after Auth0 redirect and call the claim endpoint. Use this on
 * any link that sends the user to the portal signup/login flow.
 */
export function withVisitorId(url: string): string {
  const vid = getVisitorId();
  if (!vid) return url;
  try {
    const u = new URL(url, typeof window !== "undefined" ? window.location.origin : "https://canyougrab.it");
    u.searchParams.set("vid", vid);
    return u.toString();
  } catch {
    const sep = url.includes("?") ? "&" : "?";
    return `${url}${sep}vid=${encodeURIComponent(vid)}`;
  }
}

/**
 * Call after the user completes signup — attaches any name-generation lists
 * the visitor created anonymously to their new account. Pass the user's
 * Auth0 access token. Safe to call repeatedly; the server is idempotent.
 */
export async function claimAnonLists(accessToken: string): Promise<number> {
  const visitorId = getVisitorId();
  if (!visitorId || !accessToken) return 0;
  try {
    const res = await fetch(`${API_BASE_URL}/api/names/claim`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${accessToken}`,
      },
      body: JSON.stringify({ visitor_id: visitorId }),
    });
    if (!res.ok) {
      logger.warn("Claim request failed", { status: res.status });
      return 0;
    }
    const data = await res.json();
    return Number(data?.claimed ?? 0);
  } catch (err) {
    logger.warn("Claim request errored", { error: String(err) });
    return 0;
  }
}
