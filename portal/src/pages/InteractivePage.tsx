import { useState, useRef, useEffect, useCallback, type FormEvent } from "react";
import { useAuth } from "@/hooks/useAuth";
import { useSignRequest } from "@/hooks/useSignRequest";
import { Button } from "@/components/Button";
import { API_BASE } from "@/config";

interface CheckResult {
  domain: string;
  available: boolean;
}

const EXAMPLE_DOMAINS = "myawesomestartup.com\nquicklaunch.io\nbuildfast.dev";

type CodeLang = "curl" | "python" | "javascript";

function getCodeSnippet(domains: string[], lang: CodeLang): string {
  const list = domains.map((d) => `"${d}"`).join(", ");
  switch (lang) {
    case "curl":
      return `curl -X POST https://api.canyougrab.it/api/check/bulk \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer YOUR_API_KEY" \\
  -d '{"domains": [${list}]}'`;
    case "python":
      return `import requests

res = requests.post(
    "https://api.canyougrab.it/api/check/bulk",
    headers={"Authorization": "Bearer YOUR_API_KEY"},
    json={"domains": [${list}]},
)
print(res.json()["results"])`;
    case "javascript":
      return `const res = await fetch("https://api.canyougrab.it/api/check/bulk", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "Authorization": "Bearer YOUR_API_KEY",
  },
  body: JSON.stringify({
    domains: [${list}],
  }),
});
const data = await res.json();
console.log(data.results);`;
  }
}

const TAB_LABELS: Record<CodeLang, string> = {
  curl: "cURL",
  python: "Python",
  javascript: "JavaScript",
};

const MAX_DOMAINS = 100;

export function InteractivePage() {
  const auth = useAuth();
  const { signRequest } = useSignRequest();

  const [query, setQuery] = useState(EXAMPLE_DOMAINS);
  const [results, setResults] = useState<CheckResult[]>([]);
  const [visibleCount, setVisibleCount] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [jsonResponse, setJsonResponse] = useState<string | null>(null);
  const [showJson, setShowJson] = useState(false);
  const [showCode, setShowCode] = useState(false);
  const [codeLang, setCodeLang] = useState<CodeLang>("curl");
  const [hasChecked, setHasChecked] = useState(false);
  const animRef = useRef<ReturnType<typeof setTimeout>[]>([]);

  function parseDomains(input: string): string[] {
    return input
      .split(/[\n,]+/)
      .map((d) => d.trim().toLowerCase().replace(/^https?:\/\//, ""))
      .filter(Boolean);
  }

  useEffect(() => {
    return () => animRef.current.forEach(clearTimeout);
  }, []);

  function animateResults(items: CheckResult[]) {
    animRef.current.forEach(clearTimeout);
    animRef.current = [];
    setVisibleCount(0);
    setResults(items);

    items.forEach((_, i) => {
      const t = setTimeout(() => setVisibleCount(i + 1), (i + 1) * 180);
      animRef.current.push(t);
    });
  }

  const handleSubmit = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      const domains = parseDomains(query);
      if (domains.length === 0) return;

      const batch = domains.slice(0, MAX_DOMAINS);

      setLoading(true);
      setError(null);
      setResults([]);
      setVisibleCount(0);
      setJsonResponse(null);
      setHasChecked(true);

      try {
        const req = new Request(API_BASE + "/api/portal/check/bulk", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ domains: batch }),
        });
        const signed = await signRequest(req);
        const res = await fetch(signed);
        const data = await res.json();

        if (!res.ok) {
          const detail =
            data?.detail?.message ??
            data?.detail?.error ??
            data?.detail ??
            data?.message ??
            data?.error ??
            `HTTP ${res.status}`;
          throw new Error(
            typeof detail === "string" ? detail : JSON.stringify(detail),
          );
        }

        const mapped: CheckResult[] = (data.results ?? []).map(
          (r: { domain: string; available: boolean }) => ({
            domain: r.domain,
            available: r.available,
          }),
        );
        setJsonResponse(JSON.stringify(data, null, 2));
        animateResults(mapped);
      } catch (err) {
        const msg = err instanceof Error ? err.message : "Check failed";
        setError(msg);
      } finally {
        setLoading(false);
      }
    },
    [query, signRequest],
  );

  const domainCount = parseDomains(query).length;
  const capped = domainCount > MAX_DOMAINS;
  const checkedDomains = results.map((r) => r.domain);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Interactive Checker</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Check domain availability using your API key limits.
        </p>
      </div>

      {/* Search form */}
      <form onSubmit={handleSubmit}>
        <div className="rounded-lg border border-border bg-card overflow-hidden">
          <textarea
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={"Enter domains to check...\nexample.com\nstartup.io\nmyapp.dev"}
            className="w-full bg-transparent text-foreground font-mono text-sm p-4 resize-none border-none outline-none placeholder:text-muted-foreground"
            disabled={loading}
            rows={4}
            aria-label="Enter domains to check, one per line or comma-separated"
          />
          <div className="flex items-center justify-between px-4 py-3 border-t border-border">
            <span className="text-xs text-muted-foreground">
              {domainCount} domain{domainCount !== 1 ? "s" : ""}
              {capped && (
                <span className="text-orange-400">
                  {" "}
                  (max {MAX_DOMAINS})
                </span>
              )}
            </span>
            <Button type="submit" disabled={loading || domainCount === 0}>
              {loading ? (
                <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-primary-foreground border-t-transparent" />
              ) : (
                "Check Availability"
              )}
            </Button>
          </div>
        </div>
      </form>

      {/* Error */}
      {error && (
        <p className="text-destructive text-sm text-center">{error}</p>
      )}

      {/* Results */}
      {results.length > 0 && (
        <div className="space-y-3">
          {results.map((r, i) => (
            <div
              key={r.domain}
              className={`flex items-center gap-4 px-5 py-3.5 rounded-lg border transition-all duration-300 ${
                i < visibleCount
                  ? r.available
                    ? "opacity-100 translate-y-0 border-primary/25 bg-card"
                    : "opacity-100 translate-y-0 border-destructive/15 bg-card"
                  : "opacity-0 translate-y-2 border-border bg-card"
              }`}
            >
              <span
                className={`text-lg font-bold ${
                  i < visibleCount
                    ? r.available
                      ? "text-primary"
                      : "text-destructive"
                    : ""
                }`}
              >
                {i < visibleCount ? (r.available ? "\u2713" : "\u2717") : ""}
              </span>
              <span className="font-mono text-sm flex-1">{r.domain}</span>
              <span
                className={`text-xs font-medium px-2.5 py-1 rounded-full ${
                  i < visibleCount
                    ? r.available
                      ? "bg-primary/12 text-primary"
                      : "bg-destructive/12 text-destructive"
                    : ""
                }`}
              >
                {i < visibleCount
                  ? r.available
                    ? "Available"
                    : "Taken"
                  : ""}
              </span>
            </div>
          ))}

          {/* After section: toggles */}
          {visibleCount >= results.length && (
            <div className="flex flex-col items-center gap-4 pt-4">
              <p className="text-sm text-muted-foreground">
                Checked {results.length} domain
                {results.length !== 1 ? "s" : ""} against your API key.
              </p>

              <div className="flex gap-3">
                <button
                  type="button"
                  className={`text-xs px-3 py-1.5 rounded-md border transition-colors ${
                    showJson
                      ? "border-primary text-primary"
                      : "border-border text-muted-foreground hover:text-foreground hover:border-muted-foreground"
                  }`}
                  onClick={() => {
                    setShowJson(!showJson);
                    setShowCode(false);
                  }}
                >
                  {showJson ? "Hide" : "View"} JSON Response
                </button>
                <button
                  type="button"
                  className={`text-xs px-3 py-1.5 rounded-md border transition-colors ${
                    showCode
                      ? "border-primary text-primary"
                      : "border-border text-muted-foreground hover:text-foreground hover:border-muted-foreground"
                  }`}
                  onClick={() => {
                    setShowCode(!showCode);
                    setShowJson(false);
                  }}
                >
                  {showCode ? "Hide" : "See the"} Code
                </button>
              </div>

              {showJson && jsonResponse && (
                <div className="w-full rounded-lg bg-secondary border border-border overflow-auto max-h-80">
                  <pre className="p-4 text-xs leading-relaxed">
                    <code className="font-mono text-foreground">
                      {jsonResponse}
                    </code>
                  </pre>
                </div>
              )}

              {showCode && (
                <div className="w-full">
                  <div className="flex border-b border-border" role="tablist">
                    {(Object.keys(TAB_LABELS) as CodeLang[]).map((lang) => (
                      <button
                        key={lang}
                        role="tab"
                        aria-selected={codeLang === lang}
                        className={`px-4 py-2 text-xs transition-colors border-b-2 -mb-px ${
                          codeLang === lang
                            ? "text-primary border-primary"
                            : "text-muted-foreground border-transparent hover:text-foreground"
                        }`}
                        onClick={() => setCodeLang(lang)}
                      >
                        {TAB_LABELS[lang]}
                      </button>
                    ))}
                  </div>
                  <div className="rounded-b-lg bg-secondary border border-t-0 border-border overflow-auto max-h-80">
                    <pre className="p-4 text-xs leading-relaxed">
                      <code className="font-mono text-foreground">
                        {getCodeSnippet(
                          checkedDomains.length > 0
                            ? checkedDomains
                            : ["example.com"],
                          codeLang,
                        )}
                      </code>
                    </pre>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Initial hint */}
      {!hasChecked && (
        <p className="text-center text-sm text-muted-foreground">
          Press <strong className="text-foreground">Check Availability</strong>{" "}
          to try it — these example domains work instantly.
        </p>
      )}
    </div>
  );
}
