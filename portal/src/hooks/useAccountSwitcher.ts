import { useEffect, useCallback, useSyncExternalStore } from "react";
import { useAuth0 } from "@auth0/auth0-react";

interface RememberedAccount {
  sub: string;
  email: string;
  name: string;
  pictureUrl: string;
}

const STORAGE_KEY = "cygi_remembered_accounts";

function getStoredAccounts(): RememberedAccount[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function setStoredAccounts(accounts: RememberedAccount[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(accounts));
  // Notify subscribers
  listeners.forEach((l) => l());
}

// Simple external store for cross-component reactivity
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

  const accounts = useSyncExternalStore(subscribe, getStoredAccounts);

  // Persist current user to remembered accounts on login
  useEffect(() => {
    if (!isAuthenticated || !user?.sub) return;
    const stored = getStoredAccounts();
    const exists = stored.find((a) => a.sub === user.sub);
    const entry: RememberedAccount = {
      sub: user.sub,
      email: user.email ?? "",
      name: user.name ?? "",
      pictureUrl: user.picture ?? "",
    };
    if (exists) {
      // Update in case name/picture changed
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
    setStoredAccounts(getStoredAccounts().filter((a) => a.sub !== sub));
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
