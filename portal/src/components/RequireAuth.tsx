import { ReactNode } from "react";
import { useAuth0 } from "@auth0/auth0-react";
import { useAuth } from "@/hooks/useAuth";
import { useSessionContext } from "@/hooks/SessionContext";
import { TosGate } from "@/components/TosGate";

export function RequireAuth({ children }: { children: ReactNode }) {
  const { isAuthenticated, isPending, login } = useAuth();
  const { error: authError } = useAuth0();
  const { session, sessionLoading, refreshSession } = useSessionContext();

  if (isPending) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="animate-spin rounded-full h-8 w-8 border-2 border-primary border-t-transparent" />
      </div>
    );
  }

  if (!isAuthenticated) {
    // Don't auto-redirect to login if Auth0 returned an error —
    // App.tsx will display the error message instead.
    if (!authError) {
      login();
    }
    return null;
  }

  // Wait for session data before checking TOS
  if (sessionLoading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="animate-spin rounded-full h-8 w-8 border-2 border-primary border-t-transparent" />
      </div>
    );
  }

  return (
    <TosGate
      tosAcceptedAt={session?.tos_accepted_at ?? null}
      tosVersion={session?.tos_version ?? null}
      currentTosVersion={session?.current_tos_version ?? "1.0"}
      onAccepted={refreshSession}
    >
      {children}
    </TosGate>
  );
}
