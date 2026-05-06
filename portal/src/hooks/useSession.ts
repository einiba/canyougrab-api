import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth0 } from "@auth0/auth0-react";
import { useAuth } from "@/hooks/useAuth";
import { useSignRequest } from "@/hooks/useSignRequest";
import { API_BASE, AUTH0_AUDIENCE } from "@/config";
import { claimPending } from "@/lib/anonClaim";
import { claimAnonStars } from "@/shared/name-generator";

export interface SessionData {
  tos_accepted_at: string | null;
  tos_version: string | null;
  current_tos_version: string;
}

/**
 * Calls POST /api/auth/session once after login to upsert the user record.
 * Exposes session data (including TOS status) and a refresh function.
 * Should be mounted once in AppLayout.
 */
export function useSession() {
  const { isAuthenticated, isPending } = useAuth();
  const { signRequest } = useSignRequest();
  const { getAccessTokenSilently } = useAuth0();
  const called = useRef(false);
  const [session, setSession] = useState<SessionData | null>(null);
  const [sessionLoading, setSessionLoading] = useState(true);

  const fetchSession = useCallback(async () => {
    try {
      const req = await signRequest(
        new Request(`${API_BASE}/api/auth/session`, { method: "POST" }),
      );
      const resp = await fetch(req);
      if (resp.ok) {
        const data = await resp.json();
        setSession({
          tos_accepted_at: data.tos_accepted_at ?? null,
          tos_version: data.tos_version ?? null,
          current_tos_version: data.current_tos_version ?? "1.0",
        });
        // After session lands, claim any anonymous name-generation lists the
        // user created on the marketing site before signing up. Idempotent;
        // safe to run on every login.
        claimPending(signRequest).catch(() => { /* non-critical */ });
        // Same idea for stars: any ★ the visitor placed while anon lives in
        // localStorage; POST them to the server now so they show up in
        // /starred. claimAnonStars no-ops when nothing is pending.
        const getToken = () =>
          getAccessTokenSilently({
            authorizationParams: { audience: AUTH0_AUDIENCE },
          });
        claimAnonStars(getToken).catch(() => { /* non-critical */ });
      }
    } catch {
      // Non-critical — user record will be created on next request
    } finally {
      setSessionLoading(false);
    }
  }, [signRequest, getAccessTokenSilently]);

  useEffect(() => {
    if (isPending || !isAuthenticated || called.current) return;
    called.current = true;
    fetchSession();
  }, [isAuthenticated, isPending, fetchSession]);

  // Reset when not authenticated
  useEffect(() => {
    if (!isAuthenticated && !isPending) {
      setSession(null);
      setSessionLoading(false);
    }
  }, [isAuthenticated, isPending]);

  return { session, sessionLoading, refreshSession: fetchSession };
}
