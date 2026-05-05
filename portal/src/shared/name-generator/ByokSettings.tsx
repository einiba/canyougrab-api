import { useEffect, useMemo, useState } from "react";
import { PROVIDERS, type ProviderId } from "./byok/types";
import { getAdapter } from "./byok/providers";
import {
  clearByokKey,
  readByokKey,
  readKeyForProvider,
  writeByokKey,
} from "./byok/storage";

interface Props {
  onClose: () => void;
  onSaved?: () => void;
}

type TestState = "idle" | "testing" | "ready" | "failed";

interface VerificationCache {
  models: string[];
  testedKey: string;
}

export default function ByokSettings({ onClose, onSaved }: Props) {
  const existing = readByokKey();
  const [provider, setProvider] = useState<ProviderId>(existing?.provider ?? "anthropic");
  const meta = PROVIDERS[provider];

  // Per-provider verification cache — populated on Test, kept while the modal is open.
  // For previously-saved providers we seed it with the saved model so Save & use is
  // immediately enabled (the key was tested before being saved last time).
  const [verifications, setVerifications] = useState<Partial<Record<ProviderId, VerificationCache>>>(
    () => {
      const init: Partial<Record<ProviderId, VerificationCache>> = {};
      for (const id of Object.keys(PROVIDERS) as ProviderId[]) {
        const saved = readKeyForProvider(id);
        if (saved && saved.key && saved.model) {
          init[id] = { models: [saved.model], testedKey: saved.key };
        }
      }
      return init;
    },
  );

  const initial = readKeyForProvider(provider);
  const [key, setKey] = useState(initial?.key ?? "");
  const [model, setModel] = useState(initial?.model || "");
  const [test, setTest] = useState<TestState>(verifications[provider] ? "ready" : "idle");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const saved = readKeyForProvider(provider);
    setKey(saved?.key ?? "");
    setModel(saved?.model || "");
    setTest(verifications[provider] ? "ready" : "idle");
    setError(null);
  }, [provider]); // eslint-disable-line react-hooks/exhaustive-deps

  const savedProviders = useMemo(() => {
    const set = new Set<ProviderId>();
    (Object.keys(PROVIDERS) as ProviderId[]).forEach((id) => {
      if (readKeyForProvider(id)) set.add(id);
    });
    return set;
  }, [provider, key]);

  const verification = verifications[provider];
  const isVerifiedForCurrentKey = !!verification && verification.testedKey === key.trim();
  const canSave = isVerifiedForCurrentKey && !!model;

  function handleKeyChange(v: string) {
    setKey(v);
    setError(null);
    // Editing the key invalidates any prior verification for this provider.
    if (verifications[provider]?.testedKey !== v.trim()) {
      setTest("idle");
    } else {
      setTest("ready");
    }
  }

  async function handleTest() {
    const trimmed = key.trim();
    if (!trimmed) {
      setError("Paste your API key first.");
      return;
    }
    setTest("testing");
    setError(null);
    const result = await getAdapter(provider).listModels(trimmed);
    if (result.ok && result.models && result.models.length > 0) {
      setVerifications((prev) => ({
        ...prev,
        [provider]: { models: result.models!, testedKey: trimmed },
      }));
      // Pick a sensible default: keep current model if it's in the list, else first item.
      setModel((prev) => (result.models!.includes(prev) ? prev : result.models![0]));
      setTest("ready");
    } else if (result.ok) {
      setTest("failed");
      setError("Key is valid but no chat models are available on this account.");
    } else {
      setTest("failed");
      setError(result.error ?? "Key did not validate.");
    }
  }

  function handleSave() {
    if (!canSave) return;
    writeByokKey({ provider, key: key.trim(), model });
    onSaved?.();
    onClose();
  }

  function handleClear() {
    clearByokKey(provider);
    setKey("");
    setModel("");
    setTest("idle");
    setError(null);
    setVerifications((prev) => {
      const next = { ...prev };
      delete next[provider];
      return next;
    });
    onSaved?.();
    onClose();
  }

  const modelOptions = verification?.models ?? [];
  const showModelPicker = test === "ready" && modelOptions.length > 0;

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="byok-title">
      <div className="modal-card byok-modal">
        <h3 id="byok-title" className="modal-title">Use your own AI key</h3>
        <p className="modal-body">
          Generate unlimited names using your own API key. Your key is never sent to canyougrab.it
          and is erased when you close this tab.
        </p>

        <div className="byok-tabs" role="tablist">
          {(Object.keys(PROVIDERS) as ProviderId[]).map((id) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={provider === id}
              className={`byok-tab ${provider === id ? "active" : ""}`}
              onClick={() => setProvider(id)}
            >
              {PROVIDERS[id].label}
              {savedProviders.has(id) && (
                <span className="byok-tab-dot" aria-label="Key saved" />
              )}
            </button>
          ))}
        </div>

        <label className="byok-label" htmlFor="byok-key">API key</label>
        <input
          id="byok-key"
          type="password"
          autoComplete="off"
          spellCheck={false}
          className="byok-input"
          value={key}
          onChange={(e) => handleKeyChange(e.target.value)}
          placeholder={meta.keyHint}
        />
        <div className="byok-test-row">
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={handleTest}
            disabled={!key.trim() || test === "testing"}
          >
            {test === "testing" ? "Testing…" : test === "ready" ? "Re-test" : "Test key"}
          </button>
          <span className="byok-help">
            Get a key from{" "}
            <a href={meta.consoleUrl} target="_blank" rel="noopener noreferrer" className="link-accent">
              {meta.label} console &rarr;
            </a>
          </span>
        </div>

        {showModelPicker && (
          <>
            <label className="byok-label" htmlFor="byok-model">
              Model
              <span className="byok-label-meta"> · {modelOptions.length} available</span>
            </label>
            <select
              id="byok-model"
              className="byok-select"
              value={model}
              onChange={(e) => setModel(e.target.value)}
            >
              {modelOptions.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          </>
        )}

        {!showModelPicker && (
          <p className="byok-hint">
            Test your key to fetch the list of models available on your account.
          </p>
        )}

        <div className="byok-trust">
          <strong>What happens to your key?</strong>
          <ul>
            <li>Stored in this browser tab only (sessionStorage). Erased when the tab closes.</li>
            <li>Sent directly from your browser to {meta.label}, never to canyougrab.it servers.</li>
            <li>You can clear it any time. We have no way to recover it once cleared.</li>
          </ul>
        </div>

        {error && <p className="byok-error">{error}</p>}
        {test === "ready" && !error && (
          <p className="byok-success">Key validated · {modelOptions.length} models loaded.</p>
        )}

        <div className="modal-actions byok-actions">
          <button
            type="button"
            className="btn btn-primary"
            onClick={handleSave}
            disabled={!canSave}
            title={!canSave ? "Test your key and pick a model first" : undefined}
          >
            Save &amp; use
          </button>
          {savedProviders.has(provider) && (
            <button type="button" className="btn btn-ghost" onClick={handleClear}>
              Remove key
            </button>
          )}
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
