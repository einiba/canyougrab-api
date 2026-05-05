import { useState } from "react";
import type { GeneratedName } from "./nameGen";
import { namecheapRegisterUrl, porkbunRegisterUrl } from "@/config";

interface Props {
  favorites: GeneratedName[];
  onUnpin: (domain: string) => void;
  onClear: () => void;
}

export default function FavoritesTray({ favorites, onUnpin, onClear }: Props) {
  const [showCompare, setShowCompare] = useState(false);
  const [copied, setCopied] = useState(false);

  if (favorites.length === 0) return null;

  async function handleCopyAll() {
    const text = favorites.map((f) => f.domain).join("\n");
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      window.prompt("Copy your favorites:", text);
    }
  }

  return (
    <>
      <div className="favtray" role="region" aria-label="Favorites">
        <div className="favtray-inner">
          <div className="favtray-label">
            <span className="favtray-star">★</span>
            <span>
              {favorites.length} favorite{favorites.length === 1 ? "" : "s"}
            </span>
          </div>
          <ul className="favtray-list">
            {favorites.slice(0, 6).map((f) => (
              <li key={f.domain} className="favtray-chip">
                <span className="favtray-domain">{f.domain}</span>
                <button
                  type="button"
                  onClick={() => onUnpin(f.domain)}
                  aria-label={`Unpin ${f.domain}`}
                >
                  ×
                </button>
              </li>
            ))}
            {favorites.length > 6 && (
              <li className="favtray-chip favtray-more">+{favorites.length - 6} more</li>
            )}
          </ul>
          <div className="favtray-actions">
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => setShowCompare(true)}
              disabled={favorites.length < 2}
              title={favorites.length < 2 ? "Pin 2+ to compare" : undefined}
            >
              Compare
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={handleCopyAll}
            >
              {copied ? "✓ Copied" : "Copy all"}
            </button>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={onClear}
            >
              Clear
            </button>
          </div>
        </div>
      </div>

      {showCompare && (
        <CompareModal
          favorites={favorites}
          onClose={() => setShowCompare(false)}
          onUnpin={onUnpin}
        />
      )}
    </>
  );
}

function CompareModal({
  favorites,
  onClose,
  onUnpin,
}: {
  favorites: GeneratedName[];
  onClose: () => void;
  onUnpin: (domain: string) => void;
}) {
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="compare-title">
      <div className="modal-card compare-modal">
        <div className="compare-head">
          <h3 id="compare-title" className="modal-title">Compare your favorites</h3>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>Close</button>
        </div>
        <p className="modal-body">
          Side-by-side view of every domain you've pinned. Length, extension, and registrar links —
          pick a winner and grab it.
        </p>
        <div className="compare-table-wrap">
          <table className="compare-table">
            <thead>
              <tr>
                <th>Domain</th>
                <th className="compare-num">Length</th>
                <th>TLD</th>
                <th>Status</th>
                <th>Notes</th>
                <th className="compare-actions-th">Actions</th>
              </tr>
            </thead>
            <tbody>
              {favorites.map((f) => {
                const state =
                  f.available === true ? "available" : f.available === false ? "taken" : "inconclusive";
                return (
                  <tr key={f.domain} className={`compare-row is-${state}`}>
                    <td className="compare-domain">{f.domain}</td>
                    <td className="compare-num">{f.domain.length}</td>
                    <td className="compare-tld">.{f.tld}</td>
                    <td>
                      <span className={`namegen-badge ${state}`}>
                        {state === "available" ? "Available" : state === "taken" ? "Taken" : "Inconclusive"}
                      </span>
                    </td>
                    <td className="compare-rationale text-muted">{f.rationale ?? "—"}</td>
                    <td className="compare-actions">
                      {f.available === true && (
                        <>
                          <a
                            href={namecheapRegisterUrl(f.domain)}
                            className="btn btn-primary btn-sm"
                            target="_blank"
                            rel="noopener nofollow sponsored"
                          >
                            Namecheap
                          </a>
                          <a
                            href={porkbunRegisterUrl(f.domain)}
                            className="btn btn-secondary btn-sm"
                            target="_blank"
                            rel="noopener nofollow sponsored"
                          >
                            Porkbun
                          </a>
                        </>
                      )}
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm"
                        onClick={() => onUnpin(f.domain)}
                      >
                        Unpin
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
