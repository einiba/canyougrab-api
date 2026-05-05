import { useEffect, useState } from "react";
import { useSignRequest } from "@/hooks/useSignRequest";
import { useNoIndex } from "@/hooks/useNoIndex";
import { API_BASE } from "@/config";

interface SavedListSummary {
  id: string;
  description: string;
  payload: {
    results?: Array<{ domain: string; available: boolean | null; tld?: string }>;
    styles?: string[];
    tld_pref?: string;
  };
  created_at: string | null;
  claimed_at: string | null;
}

const MARKETING_BASE = (() => {
  const host = typeof window !== "undefined" ? window.location.hostname : "";
  if (host === "localhost" || host === "127.0.0.1" || host.includes("dev")) {
    return "https://canyougrab.it";
  }
  return "https://canyougrab.it";
})();

export function SavedNamesPage() {
  useNoIndex();
  const { signRequest } = useSignRequest();
  const [lists, setLists] = useState<SavedListSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const req = await signRequest(new Request(`${API_BASE}/api/names/mine`));
        const res = await fetch(req);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (cancelled) return;
        setLists(data.lists ?? []);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load lists.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => { cancelled = true; };
  }, [signRequest]);

  if (loading) {
    return (
      <div className="max-w-4xl mx-auto px-4 py-8">
        <div className="animate-spin rounded-full h-6 w-6 border-2 border-primary border-t-transparent" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="max-w-4xl mx-auto px-4 py-8">
        <h1 className="text-2xl font-semibold mb-2">Saved name lists</h1>
        <p className="text-red-400">Couldn't load your lists: {error}</p>
      </div>
    );
  }

  if (!lists || lists.length === 0) {
    return (
      <div className="max-w-4xl mx-auto px-4 py-8">
        <h1 className="text-2xl font-semibold mb-3">Saved name lists</h1>
        <p className="text-muted-foreground mb-6">
          Lists you generate on canyougrab.it will appear here automatically once you sign in.
        </p>
        <a
          href={`${MARKETING_BASE}/find-a-name`}
          target="_blank"
          rel="noopener"
          className="inline-block px-4 py-2 rounded bg-primary text-primary-foreground text-sm font-medium hover:opacity-90"
        >
          Generate a list →
        </a>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto px-4 py-8">
      <div className="flex items-baseline justify-between mb-6">
        <h1 className="text-2xl font-semibold">Saved name lists</h1>
        <a
          href={`${MARKETING_BASE}/find-a-name`}
          target="_blank"
          rel="noopener"
          className="text-sm text-primary hover:underline"
        >
          New list →
        </a>
      </div>
      <ul className="space-y-3">
        {lists.map((list) => {
          const results = list.payload?.results ?? [];
          const available = results.filter((r) => r.available === true).length;
          const created = list.created_at ? new Date(list.created_at) : null;
          const shareUrl = `${MARKETING_BASE}/results/${list.id}`;
          const previewDomains = results
            .filter((r) => r.available === true)
            .slice(0, 3)
            .map((r) => r.domain);
          return (
            <li
              key={list.id}
              className="rounded-lg border border-border bg-card p-4 hover:border-muted-foreground/40 transition-colors"
            >
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-foreground line-clamp-2 italic">
                    &ldquo;{list.description}&rdquo;
                  </p>
                  {previewDomains.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {previewDomains.map((d) => (
                        <code key={d} className="px-2 py-0.5 rounded bg-muted/50 text-xs text-foreground">
                          {d}
                        </code>
                      ))}
                      {available > previewDomains.length && (
                        <span className="text-xs text-muted-foreground self-center">
                          +{available - previewDomains.length} more
                        </span>
                      )}
                    </div>
                  )}
                </div>
                <a
                  href={shareUrl}
                  target="_blank"
                  rel="noopener"
                  className="shrink-0 text-sm text-primary hover:underline"
                >
                  Open →
                </a>
              </div>
              <div className="mt-3 flex items-center gap-3 text-xs text-muted-foreground">
                <span>{available}/{results.length} available</span>
                {created && (
                  <>
                    <span>·</span>
                    <span>{created.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })}</span>
                  </>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
