import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { useAuth0 } from "@auth0/auth0-react";
import { useNoIndex } from "@/hooks/useNoIndex";
import { AUTH0_AUDIENCE, namecheapRegisterUrl, porkbunRegisterUrl } from "@/config";
import {
  authenticatedStarStore,
  type StarStore,
  type StoredStar,
} from "@/shared/name-generator";

const MARKETING_BASE = (() => {
  const host = typeof window !== "undefined" ? window.location.hostname : "";
  if (host === "localhost" || host === "127.0.0.1" || host.includes("dev")) {
    return "https://canyougrab.it";
  }
  return "https://canyougrab.it";
})();

type Filter = "all" | "available" | "taken";

export function StarredPage() {
  useNoIndex();
  const { getAccessTokenSilently } = useAuth0();

  const getAccessToken = useCallback(
    () =>
      getAccessTokenSilently({
        authorizationParams: { audience: AUTH0_AUDIENCE },
      }),
    [getAccessTokenSilently],
  );

  // Lazy single-instance store so unstar doesn't hop endpoints across renders.
  const storeRef = useRef<StarStore | null>(null);
  if (storeRef.current === null) {
    storeRef.current = authenticatedStarStore(getAccessToken);
  }

  const [stars, setStars] = useState<StoredStar[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");

  useEffect(() => {
    let cancelled = false;
    async function refresh() {
      try {
        const data = await storeRef.current!.list();
        if (!cancelled) setStars(data);
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "Failed to load starred names.");
        }
      }
    }
    refresh();
    const off = storeRef.current!.subscribe(refresh);
    return () => {
      cancelled = true;
      off();
    };
  }, []);

  const filtered = useMemo(() => {
    if (!stars) return [];
    return stars.filter((s) => {
      if (filter === "available") return s.available === true;
      if (filter === "taken") return s.available === false;
      return true;
    });
  }, [stars, filter]);

  async function unstar(s: StoredStar) {
    try {
      await storeRef.current!.toggle(
        { domain: s.domain, base: s.base, tld: s.tld, available: s.available },
        s.source_list_id,
      );
    } catch {
      // ignore — store handles logging; user can retry
    }
  }

  if (error) {
    return (
      <div>
        <h1 className="text-2xl font-semibold mb-2">Starred</h1>
        <p className="text-red-400">Couldn't load your stars: {error}</p>
      </div>
    );
  }

  if (stars === null) {
    return (
      <div>
        <div className="animate-spin rounded-full h-6 w-6 border-2 border-primary border-t-transparent" />
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-baseline justify-between mb-2">
        <h1 className="text-2xl font-semibold">Starred</h1>
        <a href="/interactive" className="text-sm text-primary hover:underline">
          Generate more →
        </a>
      </div>
      <p className="text-muted-foreground text-sm mb-6">
        Domains you've ★'d across all your generations. Your shortlist as you narrow down.
      </p>

      {stars.length === 0 ? (
        <div className="rounded-lg border border-border p-8 text-center text-sm text-muted-foreground">
          You haven't starred any names yet. Click the ☆ on any available domain
          on{" "}
          <a href="/interactive" className="text-primary hover:underline">
            the interactive page
          </a>{" "}
          to start your shortlist.
        </div>
      ) : (
        <>
          <div className="flex items-center gap-2 mb-4" role="tablist" aria-label="Filter">
            {(["all", "available", "taken"] as Filter[]).map((f) => (
              <button
                key={f}
                type="button"
                role="tab"
                aria-selected={filter === f}
                onClick={() => setFilter(f)}
                className={`px-3 py-1.5 rounded-md text-sm transition-colors ${
                  filter === f
                    ? "bg-primary/15 text-primary"
                    : "text-muted-foreground hover:text-foreground hover:bg-secondary"
                }`}
              >
                {f === "all" ? "All" : f === "available" ? "Available" : "Taken"}
              </button>
            ))}
            <span className="ml-auto text-xs text-muted-foreground">
              {filtered.length} of {stars.length}
            </span>
          </div>

          <ul className="space-y-2">
            {filtered.map((s) => {
              const tone =
                s.available === true
                  ? "border-primary/40"
                  : s.available === false
                  ? "border-border opacity-70"
                  : "border-border";
              const badge =
                s.available === true
                  ? <span className="text-xs px-2 py-0.5 rounded bg-primary/15 text-primary">Available</span>
                  : s.available === false
                  ? <span className="text-xs px-2 py-0.5 rounded bg-muted/40 text-muted-foreground">Taken</span>
                  : <span className="text-xs px-2 py-0.5 rounded bg-muted/40 text-muted-foreground">Inconclusive</span>;
              return (
                <li
                  key={s.domain}
                  className={`rounded-lg border ${tone} bg-card p-4 flex items-center gap-3`}
                >
                  <button
                    type="button"
                    onClick={() => unstar(s)}
                    title="Unstar"
                    aria-label={`Unstar ${s.domain}`}
                    className="text-yellow-400 hover:text-yellow-300 text-lg leading-none"
                  >
                    ★
                  </button>
                  <code className="text-base font-mono">{s.domain}</code>
                  {badge}
                  <div className="ml-auto flex items-center gap-2">
                    {s.available === true && (
                      <>
                        <a
                          href={namecheapRegisterUrl(s.domain)}
                          target="_blank"
                          rel="noopener nofollow sponsored"
                          className="text-sm px-3 py-1 rounded bg-primary text-primary-foreground hover:opacity-90"
                        >
                          Namecheap
                        </a>
                        <a
                          href={porkbunRegisterUrl(s.domain)}
                          target="_blank"
                          rel="noopener nofollow sponsored"
                          className="text-sm px-3 py-1 rounded border border-border hover:bg-secondary"
                        >
                          Porkbun
                        </a>
                      </>
                    )}
                    {s.source_list_id && (
                      <a
                        href={`${MARKETING_BASE}/results/${s.source_list_id}`}
                        target="_blank"
                        rel="noopener"
                        className="text-xs text-muted-foreground hover:text-foreground"
                        title="Open the original generation"
                      >
                        ↗ list
                      </a>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </div>
  );
}
