import { useEffect, useCallback, useSyncExternalStore } from "react";
import { useAuth0 } from "@auth0/auth0-react";

interface RememberedAccount {
  sub: string;
  email: string;
  name: string;
  pictureUrl: string;
}

const STORAGE_KEY = "cygi_remembered_accounts";

// Cache the snapshot so useSyncExternalStore gets a stable reference
let cachedRaw: string | null = null;
let cachedAccounts: RememberedAccount[] = [];

function getSnapshot(): RememberedAccount[] {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (raw !== cachedRaw) {
    cachedRaw = raw;
    try {
      cachedAccounts = raw ? JSON.parse(raw) : [];
    } catch {
      cachedAccounts = [];
    }
  }
  return cachedAccounts;
}

function setStoredAccounts(accounts: RememberedAccount[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(accounts));
  // Bust cache so next getSnapshot returns fresh data
  cachedRaw = null;
  listeners.forEach((l) => l());
}

const listeners = new Set<() => void>();
function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useAccountSwitcher() {
  const {
    user,
    isAuthenticated,
    loginWithRedirect,
    logout: auth0Logout,
  } = useAuth0();

  const accounts = useSyncExternalStore(subscribe, getSnapshot);

  // Persist current user to remembered accounts on login
  useEffect(() => {
    if (!isAuthenticated || !user?.sub) return;
    const stored = getSnapshot();
    const exists = stored.find((a) => a.sub === user.sub);
    const entry: RememberedAccount = {
      sub: user.sub,
      email: user.email ?? "",
      name: user.name ?? "",
      pictureUrl: user.picture ?? "",
    };
    if (exists) {
      // Only update if something actually changed
      if (
        exists.email === entry.email &&
        exists.name === entry.name &&
        exists.pictureUrl === entry.pictureUrl
      ) {
        return;
      }
      setStoredAccounts(
        stored.map((a) => (a.sub === user.sub ? entry : a)),
      );
    } else {
      setStoredAccounts([...stored, entry]);
    }
  }, [isAuthenticated, user?.sub, user?.email, user?.name, user?.picture]);

  const switchAccount = useCallback(
    (email: string) => {
      loginWithRedirect({
        authorizationParams: { login_hint: email },
      });
    },
    [loginWithRedirect],
  );

  const addAccount = useCallback(() => {
    loginWithRedirect({
      authorizationParams: { prompt: "login" as const },
    });
  }, [loginWithRedirect]);

  const removeAccount = useCallback((sub: string) => {
    setStoredAccounts(getSnapshot().filter((a) => a.sub !== sub));
  }, []);

  const signOut = useCallback(() => {
    auth0Logout({ logoutParams: { returnTo: window.location.origin } });
  }, [auth0Logout]);

  return {
    accounts,
    currentSub: user?.sub ?? null,
    switchAccount,
    addAccount,
    removeAccount,
    signOut,
  };
}
