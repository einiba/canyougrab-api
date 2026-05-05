import { useEffect, useMemo, useState, type FormEvent } from "react";
import { logger } from "@/lib/logger";
import "./styles.css";
import {
  ByokLimitError,
  checkDomainsOnly,
  generateNames,
  type GenerateNamesResponse,
  type NameStyle,
  type TldPreference,
} from "./nameGen";
import { withVisitorId } from "./visitor";
import { API_BASE_URL } from "@/config";
import ByokIndicator from "./ByokIndicator";
import ByokSettings from "./ByokSettings";
import NameCard from "./NameCard";
import FavoritesTray from "./FavoritesTray";
import { readByokKey, subscribe as subscribeByok } from "./byok/storage";
import { PROVIDERS } from "./byok/types";
import type { GeneratedName } from "./nameGen";

type Mode = "generate" | "check";

interface NameGeneratorProps {
  /**
   * When provided, Check mode uses the portal-authenticated bulk-check
   * endpoint and sends this token as a bearer header. Marketing site (anon)
   * leaves this undefined and Check mode falls back to /api/names/check.
   */
  getAccessToken?: () => Promise<string>;
  /**
   * Hide the BYOK gate even when no key is set. Reserved for future paid
   * hosted-LLM tiers — currently unused; both apps still require BYOK to
   * generate.
   */
  hideByokGate?: boolean;
  /** Initial tab. Defaults to "generate". */
  defaultMode?: Mode;
}

const STYLE_OPTIONS: { value: NameStyle; label: string }[] = [
  { value: "modern", label: "Modern" },
  { value: "playful", label: "Playful" },
  { value: "professional", label: "Professional" },
  { value: "short", label: "Short" },
  { value: "wordplay", label: "Wordplay" },
  { value: "compound", label: "Compound" },
];

const TLD_OPTIONS: { value: TldPreference; label: string; hint: string }[] = [
  { value: "com_only", label: ".com only", hint: "Classic and trusted" },
  { value: "tech", label: "Tech", hint: ".io, .dev, .ai, .app" },
  { value: "global", label: "Global", hint: ".co, .net, .org, .com" },
  { value: "any", label: "Any", hint: "Show me everything" },
];

type AvailabilityFilter = "all" | "available" | "taken";

const PLACEHOLDER =
  "e.g. A subscription box for indie board game designers — we ship one playtest-ready prototype kit per month, with components and a private feedback community.";

function useByokKey() {
  const [key, setKey] = useState(() => readByokKey());
  useEffect(() => subscribeByok(() => setKey(readByokKey())), []);
  return key;
}

export default function NameGenerator({
  getAccessToken,
  hideByokGate = false,
  defaultMode = "generate",
}: NameGeneratorProps = {}) {
  const byok = useByokKey();
  const [mode, setMode] = useState<Mode>(defaultMode);
  const [description, setDescription] = useState("");
  const [styles, setStyles] = useState<Set<NameStyle>>(new Set(["modern"]));
  const [tldPref, setTldPref] = useState<TldPreference>("any");
  const [response, setResponse] = useState<GenerateNamesResponse | null>(null);
  const [checkResults, setCheckResults] = useState<GeneratedName[] | null>(null);
  const [pasteInput, setPasteInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasGenerated, setHasGenerated] = useState(false);
  const [filter, setFilter] = useState<AvailabilityFilter>("available");
  const [tldFilter, setTldFilter] = useState<string>("all");
  const [showByok, setShowByok] = useState(false);
  const [copiedShare, setCopiedShare] = useState(false);
  const [byokLimit, setByokLimit] = useState<ByokLimitError | null>(null);
  // Favorites: pinned domains across the session, regardless of which generation
  // they came from. Used by the FavoritesTray + Compare view.
  const [favorites, setFavorites] = useState<Map<string, GeneratedName>>(() => new Map());
  // Anchors: bases the user wants more like — biases the next generation.
  const [anchors, setAnchors] = useState<Set<string>>(() => new Set());

  function switchMode(next: Mode) {
    if (next === mode) return;
    setMode(next);
    setError(null);
    setByokLimit(null);
    setHasGenerated(false);
    setResponse(null);
    setCheckResults(null);
    setFilter("available");
    setTldFilter("all");
  }

  function togglePin(domain: string) {
    setFavorites((prev) => {
      const next = new Map(prev);
      if (next.has(domain)) {
        next.delete(domain);
      } else {
        const pool = mode === "check" ? checkResults ?? [] : response?.results ?? [];
        const r = pool.find((x) => x.domain === domain);
        if (r) next.set(domain, r);
      }
      return next;
    });
  }

  function toggleAnchor(base: string) {
    setAnchors((prev) => {
      const next = new Set(prev);
      if (next.has(base)) next.delete(base);
      else next.add(base);
      return next;
    });
  }

  async function handleCopyShareLink() {
    if (!response?.listId) return;
    const url = `${API_BASE_URL}/share/${response.listId}`;
    try {
      await navigator.clipboard.writeText(url);
      setCopiedShare(true);
      setTimeout(() => setCopiedShare(false), 2000);
    } catch {
      window.prompt("Copy this share link:", url);
    }
  }

  function toggleStyle(s: NameStyle) {
    setStyles((prev) => {
      const next = new Set(prev);
      if (next.has(s)) next.delete(s);
      else next.add(s);
      return next;
    });
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!byok) {
      setError("Add your AI key to start generating names.");
      setShowByok(true);
      return;
    }
    if (description.trim().length < 10) {
      setError("Tell us a bit more about your business — at least a sentence.");
      return;
    }
    setLoading(true);
    setError(null);
    setByokLimit(null);
    setResponse(null);
    setHasGenerated(true);
    try {
      const exclude = Array.from(
        new Set((response?.results ?? []).map((r) => r.base).filter(Boolean)),
      ).slice(0, 60);
      const data = await generateNames({
        description: description.trim(),
        styles: Array.from(styles),
        tldPreference: tldPref,
        count: 36,
        anchors: anchors.size > 0 ? Array.from(anchors) : undefined,
        exclude,
      });
      setResponse(data);
      logger.info("Names generated", {
        count: data.results.length,
        mode: data.mode,
        anchors: anchors.size,
        excluded: exclude.length,
      });
    } catch (err) {
      if (err instanceof ByokLimitError) {
        logger.info("BYOK daily limit reached", { dailyLimit: err.dailyLimit });
        setByokLimit(err);
      } else {
        const msg = err instanceof Error ? err.message : "Something went wrong.";
        logger.error("Name generation failed", { error: msg });
        setError(msg);
      }
    } finally {
      setLoading(false);
    }
  }

  async function handleCheckSubmit(e: FormEvent) {
    e.preventDefault();
    const lines = pasteInput
      .split(/[\s,;\n]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    if (lines.length === 0) {
      setError("Paste at least one domain — one per line is fine.");
      return;
    }
    setLoading(true);
    setError(null);
    setByokLimit(null);
    setCheckResults(null);
    setHasGenerated(true);
    try {
      const out = await checkDomainsOnly(
        lines,
        getAccessToken ? { getAccessToken } : undefined,
      );
      if (out.length === 0) {
        setError(
          "No valid domains found. Try pasting full hostnames like `myidea.com` (one per line).",
        );
      }
      setCheckResults(out);
      logger.info("Domains checked", {
        input: lines.length,
        valid: out.length,
        portal: Boolean(getAccessToken),
      });
    } catch (err) {
      if (err instanceof ByokLimitError) {
        logger.info("BYOK daily limit reached on check", { dailyLimit: err.dailyLimit });
        setByokLimit(err);
      } else {
        const msg = err instanceof Error ? err.message : "Something went wrong.";
        logger.error("Domain check failed", { error: msg });
        setError(msg);
      }
    } finally {
      setLoading(false);
    }
  }

  const results = mode === "check" ? checkResults ?? [] : response?.results ?? [];

  const availableTlds = useMemo(() => {
    const set = new Set<string>();
    results.forEach((r) => set.add(r.tld));
    return Array.from(set).sort();
  }, [results]);

  const filtered = useMemo(() => {
    return results.filter((r) => {
      if (filter === "available" && r.available !== true) return false;
      if (filter === "taken" && r.available !== false) return false;
      if (tldFilter !== "all" && r.tld !== tldFilter) return false;
      return true;
    });
  }, [results, filter, tldFilter]);

  const availableCount = results.filter((r) => r.available === true).length;
  const submitDisabled = loading || description.trim().length === 0 || !byok;

  const showGenerateForm = mode === "generate" && (byok || hideByokGate);
  const showByokGate = mode === "generate" && !byok && !hideByokGate;

  return (
    <div className="namegen">
      <div className="namegen-mode-tabs" role="tablist" aria-label="Name generation mode">
        <button
          type="button"
          role="tab"
          aria-selected={mode === "generate"}
          className={`namegen-mode-tab ${mode === "generate" ? "active" : ""}`}
          onClick={() => switchMode("generate")}
          disabled={loading}
        >
          ✨ Generate names
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={mode === "check"}
          className={`namegen-mode-tab ${mode === "check" ? "active" : ""}`}
          onClick={() => switchMode("check")}
          disabled={loading}
        >
          🔍 Check my list
        </button>
      </div>

      <div className="namegen-byok-row">
        <ByokIndicator onOpen={() => setShowByok(true)} />
      </div>

      {showByokGate && <ByokRequiredPanel onAdd={() => setShowByok(true)} />}

      {mode === "check" && (
        <form onSubmit={handleCheckSubmit} className="namegen-form">
          <label className="namegen-label" htmlFor="namegen-paste">
            Paste domains to check — one per line
          </label>
          <textarea
            id="namegen-paste"
            className="namegen-textarea"
            value={pasteInput}
            onChange={(e) => setPasteInput(e.target.value)}
            placeholder={"myidea.com\nmyidea.io\nmyidea.app\n…"}
            rows={6}
            spellCheck={false}
            autoCapitalize="off"
            autoCorrect="off"
            disabled={loading}
          />
          <p className="namegen-hint text-muted">
            Up to 100 domains. We'll skip duplicates and run live DNS + WHOIS on each one.
          </p>
          <button
            type="submit"
            className="btn btn-primary btn-large namegen-submit"
            disabled={loading || pasteInput.trim().length === 0}
          >
            {loading ? "Checking…" : "Check availability"}
          </button>
        </form>
      )}

      {showGenerateForm && (
        <>
          <form onSubmit={handleSubmit} className="namegen-form">
            <label className="namegen-label" htmlFor="namegen-desc">
              Describe your business in a sentence or two
            </label>
            <textarea
              id="namegen-desc"
              className="namegen-textarea"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={PLACEHOLDER}
              rows={4}
              disabled={loading}
            />

            <div className="namegen-controls">
              <div className="namegen-control-group">
                <span className="namegen-control-label">Style</span>
                <div className="namegen-chips">
                  {STYLE_OPTIONS.map((opt) => (
                    <button
                      type="button"
                      key={opt.value}
                      className={`namegen-chip ${styles.has(opt.value) ? "active" : ""}`}
                      onClick={() => toggleStyle(opt.value)}
                      disabled={loading}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="namegen-control-group">
                <span className="namegen-control-label">Domain extension</span>
                <div className="namegen-chips">
                  {TLD_OPTIONS.map((opt) => (
                    <button
                      type="button"
                      key={opt.value}
                      className={`namegen-chip ${tldPref === opt.value ? "active" : ""}`}
                      onClick={() => setTldPref(opt.value)}
                      disabled={loading}
                      title={opt.hint}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            {anchors.size > 0 && (
              <div className="namegen-anchors" aria-label="Anchors for next generation">
                <span className="namegen-anchors-label">More like:</span>
                {Array.from(anchors).map((a) => (
                  <span key={a} className="namegen-anchor-chip">
                    {a}
                    <button
                      type="button"
                      onClick={() => toggleAnchor(a)}
                      aria-label={`Remove anchor ${a}`}
                    >
                      ×
                    </button>
                  </span>
                ))}
                <button
                  type="button"
                  className="namegen-anchor-clear"
                  onClick={() => setAnchors(new Set())}
                >
                  clear
                </button>
              </div>
            )}

            <button
              type="submit"
              className="btn btn-primary btn-large namegen-submit"
              disabled={submitDisabled}
            >
              {loading
                ? "Generating names…"
                : anchors.size > 0
                ? `Generate more like ${Array.from(anchors).slice(0, 2).join(", ")}${anchors.size > 2 ? "…" : ""}`
                : hasGenerated
                ? "Generate more names"
                : "Find me a name"}
            </button>
          </form>
        </>
      )}

      {error && <p className="demo-error namegen-error">{error}</p>}

      {byokLimit && <SoftPaywall limit={byokLimit} />}

      {hasGenerated && !loading && results.length > 0 && (
        <div className="namegen-results-area">
          <div className="namegen-summary">
            <strong>{availableCount}</strong> of {results.length}{" "}
            {mode === "check" ? "look available." : "suggestions look available."}{" "}
            <span className="text-muted">Live DNS + WHOIS lookup.</span>
            {mode === "generate" && response?.listId && (
              <span className="namegen-summary-actions">
                <button
                  type="button"
                  className="namegen-share-btn"
                  onClick={handleCopyShareLink}
                >
                  {copiedShare ? "✓ Link copied" : "Copy share link"}
                </button>
                <a
                  href={response.signupUrl}
                  className="namegen-share-btn namegen-save-btn"
                >
                  Save to account
                </a>
              </span>
            )}
          </div>

          <div className="namegen-filters">
            <div className="namegen-filter-group" role="tablist" aria-label="Filter by availability">
              {(["available", "all", "taken"] as AvailabilityFilter[]).map((f) => (
                <button
                  key={f}
                  type="button"
                  role="tab"
                  aria-selected={filter === f}
                  className={`namegen-filter ${filter === f ? "active" : ""}`}
                  onClick={() => setFilter(f)}
                >
                  {f === "available" ? "Available" : f === "taken" ? "Taken" : "All"}
                </button>
              ))}
            </div>

            {availableTlds.length > 1 && (
              <select
                className="namegen-tld-select"
                value={tldFilter}
                onChange={(e) => setTldFilter(e.target.value)}
                aria-label="Filter by TLD"
              >
                <option value="all">All extensions</option>
                {availableTlds.map((t) => (
                  <option key={t} value={t}>
                    .{t}
                  </option>
                ))}
              </select>
            )}
          </div>

          {filtered.length === 0 ? (
            <p className="namegen-empty">
              {mode === "check"
                ? "No matches with these filters."
                : "No matches with these filters. Try widening the filter or generating again with a different style."}
            </p>
          ) : (
            <ul className="namegen-grid">
              {filtered.map((r) => (
                <NameCard
                  key={r.domain}
                  result={r}
                  signupUrl={response?.signupUrl}
                  pinned={favorites.has(r.domain)}
                  anchored={mode === "generate" && anchors.has(r.base)}
                  onTogglePin={togglePin}
                  onToggleAnchor={mode === "generate" ? toggleAnchor : undefined}
                />
              ))}
            </ul>
          )}
        </div>
      )}

      {hasGenerated && !loading && results.length === 0 && !error && !byokLimit && (
        <p className="namegen-empty">
          {mode === "check"
            ? "No valid domains found in your input. Paste full hostnames like `myidea.com` (one per line)."
            : "No suggestions yet — try rephrasing your description."}
        </p>
      )}

      {showByok && <ByokSettings onClose={() => setShowByok(false)} />}

      <FavoritesTray
        favorites={Array.from(favorites.values())}
        onUnpin={togglePin}
        onClear={() => setFavorites(new Map())}
      />
    </div>
  );
}

function SoftPaywall({ limit }: { limit: ByokLimitError }) {
  const signupUrl = withVisitorId(limit.signupUrl);
  return (
    <div className="softpaywall">
      <div className="softpaywall-icon" aria-hidden>🚀</div>
      <h3 className="softpaywall-title">You've hit today's free limit</h3>
      <p className="softpaywall-body">
        You've used your {limit.dailyLimit} free availability checks today. Generation worked — your
        AI key kept that part free — but the live DNS + WHOIS pipeline costs us each lookup. Sign up
        and we'll lift the cap.
      </p>
      <div className="softpaywall-actions">
        <a href={signupUrl} className="btn btn-primary btn-large">
          Create free account &amp; keep going
        </a>
        <span className="text-muted softpaywall-foot">
          Or come back tomorrow — the cap resets every 24h.
        </span>
      </div>
    </div>
  );
}

function ByokRequiredPanel({ onAdd }: { onAdd: () => void }) {
  const providerLabels = (Object.keys(PROVIDERS) as Array<keyof typeof PROVIDERS>)
    .map((p) => PROVIDERS[p].label.replace(/\s*\(.*\)/, ""))
    .join(", ");
  return (
    <div className="byok-required">
      <div className="byok-required-icon" aria-hidden>🔑</div>
      <h3 className="byok-required-title">Add your AI key to start generating</h3>
      <p className="byok-required-body">
        canyougrab.it uses your own {providerLabels} key to generate name ideas.
        Your key is sent directly from your browser to your provider — never to our
        servers — and is erased when you close this tab.
      </p>
      <button type="button" className="btn btn-primary btn-large" onClick={onAdd}>
        Add my AI key
      </button>
      <p className="byok-required-foot text-muted">
        Don't have one? Get a key in 30 seconds:{" "}
        <a
          href={PROVIDERS.gemini.consoleUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="link-accent"
        >
          Google AI Studio (free tier)
        </a>
        ,{" "}
        <a
          href={PROVIDERS.anthropic.consoleUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="link-accent"
        >
          Anthropic
        </a>
        , or{" "}
        <a
          href={PROVIDERS.openai.consoleUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="link-accent"
        >
          OpenAI
        </a>
        .
      </p>
    </div>
  );
}
