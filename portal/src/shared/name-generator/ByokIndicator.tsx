import { useEffect, useState } from "react";
import { PROVIDERS } from "./byok/types";
import { clearByokKey, readByokKey, subscribe } from "./byok/storage";

interface Props {
  onOpen: () => void;
}

export default function ByokIndicator({ onOpen }: Props) {
  const [key, setKey] = useState(readByokKey());

  useEffect(() => subscribe(() => setKey(readByokKey())), []);

  if (!key) {
    return (
      <button type="button" className="byok-pill byok-pill-empty" onClick={onOpen}>
        <span aria-hidden>🔑</span>
        <span>Use your own AI key</span>
      </button>
    );
  }

  const meta = PROVIDERS[key.provider];
  return (
    <div className="byok-pill byok-pill-active">
      <span aria-hidden>🔒</span>
      <span>Using your {meta.label} key</span>
      <button type="button" className="byok-pill-action" onClick={onOpen}>
        Change
      </button>
      <button
        type="button"
        className="byok-pill-action"
        onClick={() => { clearByokKey(); }}
        aria-label="Clear key"
      >
        Clear
      </button>
    </div>
  );
}
