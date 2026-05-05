import { namecheapRegisterUrl, porkbunRegisterUrl } from "@/config";
import type { GeneratedName } from "./nameGen";

interface Props {
  result: GeneratedName;
  signupUrl?: string;
  pinned?: boolean;
  anchored?: boolean;
  onTogglePin?: (domain: string) => void;
  onToggleAnchor?: (base: string) => void;
}

export default function NameCard({
  result,
  signupUrl,
  pinned,
  anchored,
  onTogglePin,
  onToggleAnchor,
}: Props) {
  if (result.locked) {
    return (
      <li className="namegen-card is-locked">
        {signupUrl && (
          <div className="namegen-locked-overlay">
            <a href={signupUrl} className="btn btn-primary btn-sm">
              Sign up to unlock
            </a>
          </div>
        )}
        <div className="namegen-card-head">
          <span className="namegen-domain namegen-domain-blurred">example.com</span>
          <span className="namegen-badge available">Available</span>
        </div>
        <p className="namegen-rationale namegen-domain-blurred">Hidden — sign up to view.</p>
      </li>
    );
  }

  const state =
    result.available === true ? "available" : result.available === false ? "taken" : "inconclusive";
  const label =
    state === "available" ? "Available" : state === "taken" ? "Taken" : "Inconclusive";

  return (
    <li className={`namegen-card is-${state} ${pinned ? "is-pinned" : ""} ${anchored ? "is-anchored" : ""}`}>
      <div className="namegen-card-head">
        <span className="namegen-domain">{result.domain}</span>
        <div className="namegen-card-head-right">
          {result.available === true && onToggleAnchor && (
            <button
              type="button"
              className={`namegen-icon-btn ${anchored ? "active" : ""}`}
              onClick={() => onToggleAnchor(result.base)}
              title={anchored ? "Stop using as anchor" : "Generate more like this"}
              aria-label={anchored ? "Anchored — click to remove" : "More names like this"}
            >
              {anchored ? "🎯" : "↻"}
            </button>
          )}
          {result.available === true && onTogglePin && (
            <button
              type="button"
              className={`namegen-icon-btn ${pinned ? "active pin-active" : ""}`}
              onClick={() => onTogglePin(result.domain)}
              title={pinned ? "Remove from favorites" : "Save to favorites"}
              aria-label={pinned ? "Pinned" : "Pin to favorites"}
            >
              {pinned ? "★" : "☆"}
            </button>
          )}
          <span className={`namegen-badge ${state}`}>{label}</span>
        </div>
      </div>
      {result.rationale && <p className="namegen-rationale">{result.rationale}</p>}
      {result.available === true ? (
        <div className="namegen-actions">
          <a
            href={namecheapRegisterUrl(result.domain)}
            className="btn btn-primary btn-sm"
            target="_blank"
            rel="noopener nofollow sponsored"
          >
            Register on Namecheap
          </a>
          <a
            href={porkbunRegisterUrl(result.domain)}
            className="btn btn-secondary btn-sm"
            target="_blank"
            rel="noopener nofollow sponsored"
          >
            Porkbun
          </a>
        </div>
      ) : result.available === false ? (
        <p className="namegen-card-note text-muted">Already registered.</p>
      ) : (
        <p className="namegen-card-note text-muted">Couldn't confirm — try again or check directly.</p>
      )}
    </li>
  );
}
