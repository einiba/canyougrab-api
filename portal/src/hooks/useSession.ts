import { useEffect, useRef } from "react";
import { useAuth } from "@/hooks/useAuth";
import { useSignRequest } from "@/hooks/useSignRequest";
import { API_BASE } from "@/config";

/**
 * Calls POST /api/auth/session once after login to upsert the user record.
 * Should be mounted once in AppLayout.
 */
export function useSession() {
  const { isAuthenticated, isPending } = useAuth();
  const { signRequest } = useSignRequest();
  const called = useRef(false);

  useEffect(() => {
    if (isPending || !isAuthenticated || called.current) return;
    called.current = true;

    (async () => {
      try {
        const req = await signRequest(
          new Request(`${API_BASE}/api/auth/session`, { method: "POST" }),
        );
        await fetch(req);
      } catch {
        // Non-critical — user record will be created on next request
      }
    })();
  }, [isAuthenticated, isPending, signRequest]);
}
