import { createContext, useContext, ReactNode } from "react";
import { SessionData, useSession } from "@/hooks/useSession";

interface SessionContextValue {
  session: SessionData | null;
  sessionLoading: boolean;
  refreshSession: () => Promise<void>;
}

const SessionContext = createContext<SessionContextValue>({
  session: null,
  sessionLoading: true,
  refreshSession: async () => {},
});

export function SessionProvider({ children }: { children: ReactNode }) {
  const value = useSession();
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSessionContext() {
  return useContext(SessionContext);
}
