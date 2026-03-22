import { useState, useRef, useEffect } from "react";
import { useAuth } from "@/hooks/useAuth";
import { useAccountSwitcher } from "@/hooks/useAccountSwitcher";
import { Avatar } from "@/components/Avatar";

export function UserProfileDropdown() {
  const { profile } = useAuth();
  const { accounts, currentSub, switchAccount, addAccount, removeAccount, signOut } =
    useAccountSwitcher();
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // Close on click outside
  useEffect(() => {
    if (!open) return;
    function handleClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("mousedown", handleClick);
      document.removeEventListener("keydown", handleKey);
    };
  }, [open]);

  if (!profile) return null;

  const otherAccounts = accounts.filter((a) => a.sub !== currentSub);

  return (
    <div ref={containerRef} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        className="rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring cursor-pointer"
        aria-label="Account menu"
        aria-expanded={open}
      >
        <Avatar src={profile.pictureUrl} name={profile.name} size="md" />
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-2 w-80 rounded-xl border border-border bg-card shadow-2xl z-50 overflow-hidden">
          {/* Current user */}
          <div className="px-5 pt-5 pb-4 flex flex-col items-center text-center">
            <Avatar src={profile.pictureUrl} name={profile.name} size="lg" />
            <p className="mt-3 text-sm font-medium text-foreground">
              {profile.name}
            </p>
            <p className="text-xs text-muted-foreground">{profile.email}</p>
          </div>

          <div className="border-t border-border" />

          {/* Other remembered accounts */}
          {otherAccounts.length > 0 && (
            <>
              <div className="py-1">
                {otherAccounts.map((account) => (
                  <div
                    key={account.sub}
                    className="flex items-center gap-3 px-4 py-2.5 hover:bg-secondary/60 group"
                  >
                    <Avatar
                      src={account.pictureUrl}
                      name={account.name}
                      size="sm"
                    />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-foreground truncate">
                        {account.name}
                      </p>
                      <p className="text-xs text-muted-foreground truncate">
                        {account.email}
                      </p>
                    </div>
                    <div className="flex items-center gap-1">
                      <button
                        onClick={() => {
                          setOpen(false);
                          switchAccount(account.email);
                        }}
                        className="text-xs text-primary hover:text-primary/80 font-medium px-2 py-1 rounded hover:bg-primary/10 cursor-pointer"
                      >
                        Switch
                      </button>
                      <button
                        onClick={() => removeAccount(account.sub)}
                        className="text-xs text-muted-foreground hover:text-destructive px-1.5 py-1 rounded hover:bg-destructive/10 opacity-0 group-hover:opacity-100 transition-opacity cursor-pointer"
                        aria-label={`Remove ${account.name}`}
                      >
                        &times;
                      </button>
                    </div>
                  </div>
                ))}
              </div>
              <div className="border-t border-border" />
            </>
          )}

          {/* Add account */}
          <button
            onClick={() => {
              setOpen(false);
              addAccount();
            }}
            className="w-full flex items-center gap-3 px-4 py-2.5 text-sm text-foreground hover:bg-secondary/60 cursor-pointer"
          >
            <span className="w-6 h-6 rounded-full flex items-center justify-center bg-secondary text-muted-foreground text-base">
              +
            </span>
            Add another account
          </button>

          <div className="border-t border-border" />

          {/* Sign out */}
          <button
            onClick={() => {
              setOpen(false);
              signOut();
            }}
            className="w-full flex items-center justify-center gap-2 px-4 py-3 text-sm text-muted-foreground hover:text-foreground hover:bg-secondary/60 cursor-pointer"
          >
            <svg
              className="w-4 h-4"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
              strokeWidth={1.5}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M15.75 9V5.25A2.25 2.25 0 0013.5 3h-6a2.25 2.25 0 00-2.25 2.25v13.5A2.25 2.25 0 007.5 21h6a2.25 2.25 0 002.25-2.25V15m3-3h-9m9 0l-3-3m3 3l-3 3"
              />
            </svg>
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}
