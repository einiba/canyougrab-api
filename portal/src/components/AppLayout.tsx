import { NavLink, Outlet } from "react-router";
import { useAuth } from "@/hooks/useAuth";
import { SessionProvider } from "@/hooks/SessionContext";
import { Button } from "@/components/Button";
import { UserProfileDropdown } from "@/components/UserProfileDropdown";

const navLinks = [
  { to: "/usage", label: "Usage & Billing", auth: true },
  { to: "/api-keys", label: "API Keys", auth: true },
  { to: "/interactive", label: "Interactive", auth: true },
  { to: "/pricing", label: "Plans & Pricing", auth: false },
  { to: "/docs", label: "API Reference", auth: false },
];

function AppLayoutInner() {
  const { isAuthenticated, isPending, profile, login, logout } = useAuth();

  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b border-border">
        <div className="max-w-6xl mx-auto px-4 h-14 flex items-center justify-between">
          <div className="flex items-center gap-8">
            <a href="/" className="flex items-center gap-2">
              <img src="/logo-dark.svg" alt="CanYouGrab" className="h-7" />
            </a>
            <nav className="hidden md:flex items-center gap-1">
              {navLinks
                .filter((l) => !l.auth || isAuthenticated)
                .map((link) => (
                  <NavLink
                    key={link.to}
                    to={link.to}
                    className={({ isActive }) =>
                      `px-3 py-1.5 text-sm rounded-md transition-colors ${
                        isActive
                          ? "text-primary bg-primary/10"
                          : "text-muted-foreground hover:text-foreground hover:bg-secondary"
                      }`
                    }
                  >
                    {link.label}
                  </NavLink>
                ))}
            </nav>
          </div>
          <div>
            {isPending ? null : isAuthenticated ? (
              <UserProfileDropdown />
            ) : (
              <Button onClick={login} className="text-sm">
                Sign In
              </Button>
            )}
          </div>
        </div>
      </header>
      <main className="flex-1 max-w-6xl mx-auto px-4 py-8 w-full">
        <Outlet />
      </main>
      <footer className="border-t border-border py-4">
        <div className="max-w-6xl mx-auto px-4 flex items-center justify-between text-xs text-muted-foreground">
          <span>&copy; {new Date().getFullYear()} CanYouGrab</span>
          <NavLink to="/terms" className="hover:text-foreground transition-colors">
            Terms of Service
          </NavLink>
        </div>
      </footer>
    </div>
  );
}

export function AppLayout() {
  return (
    <SessionProvider>
      <AppLayoutInner />
    </SessionProvider>
  );
}
