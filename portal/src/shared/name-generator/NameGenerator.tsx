import { useEffect, useMemo, useState, type FormEvent } from "react";
import { logger } from "@/lib/logger";
import "./styles.css";
import {
  ByokLimitError,
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

export default function NameGenerator() {
  const byok = useByokKey();
  const [description, setDescription] = useState("");
  const [styles, setStyles] = useState<Set<NameStyle>>(new Set(["modern"]));
  const [tldPref, setTldPref] = useState<TldPreference>("any");
  const [response, setResponse] = useState<GenerateNamesResponse | null>(null);
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

  function togglePin(domain: string) {
    setFavorites((prev) => {
      const next = new Map(prev);
      if (next.has(domain)) {
        next.delete(domain);
      } else {
        const r = (response?.results ?? []).find((x) => x.domain === domain);
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

  const results = response?.results ?? [];

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

  return (
    <div className="namegen">
      <div className="namegen-byok-row">
        <ByokIndicator onOpen={() => setShowByok(true)} />
      </div>

      {!byok && <ByokRequiredPanel onAdd={() => setShowByok(true)} />}

      {byok && (
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

          {error && <p className="demo-error namegen-error">{error}</p>}

          {byokLimit && <SoftPaywall limit={byokLimit} />}

          {hasGenerated && !loading && response && results.length > 0 && (
            <div className="namegen-results-area">
              <div className="namegen-summary">
                <strong>{availableCount}</strong> of {results.length} suggestions look available.{" "}
                <span className="text-muted">Live DNS + WHOIS lookup.</span>
                {response.listId && (
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
                  No matches with these filters. Try widening the filter or generating again with a different style.
                </p>
              ) : (
                <ul className="namegen-grid">
                  {filtered.map((r) => (
                    <NameCard
                      key={r.domain}
                      result={r}
                      signupUrl={response.signupUrl}
                      pinned={favorites.has(r.domain)}
                      anchored={anchors.has(r.base)}
                      onTogglePin={togglePin}
                      onToggleAnchor={toggleAnchor}
                    />
                  ))}
                </ul>
              )}
            </div>
          )}

          {hasGenerated && !loading && response && results.length === 0 && !error && (
            <p className="namegen-empty">No suggestions yet — try rephrasing your description.</p>
          )}
        </>
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
