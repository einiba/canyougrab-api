import { useState, useEffect, useCallback } from "react";
import { useSignRequest } from "@/hooks/useSignRequest";
import { useNoIndex } from "@/hooks/useNoIndex";
import { Button } from "@/components/Button";
import { API_BASE } from "@/config";
import { getTurnstileToken } from "@/lib/turnstile";

interface ApiKey {
  id: string;
  key_prefix: string;
  description: string;
  plan: string;
  created_at: string | null;
  disabled_at: string | null;
  active: boolean;
}

export function ApiKeysPage() {
  useNoIndex();
  const { signRequest } = useSignRequest();
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Create key state
  const [creating, setCreating] = useState(false);
  const [newKeyDescription, setNewKeyDescription] = useState("");
  const [showCreateForm, setShowCreateForm] = useState(false);

  // Revealed raw key (shown only once after create/rotate)
  const [revealedKey, setRevealedKey] = useState<{ id: string; raw: string } | null>(null);
  const [copied, setCopied] = useState(false);

  // Confirm dialogs
  const [confirmRotate, setConfirmRotate] = useState<string | null>(null);
  const [confirmDisable, setConfirmDisable] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  const fetchKeys = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const req = new Request(`${API_BASE}/api/keys`);
      const signed = await signRequest(req);
      const res = await fetch(signed);
      if (!res.ok) throw new Error(`Failed to load keys (${res.status})`);
      const json: ApiKey[] = await res.json();
      setKeys(json.filter((k) => k.active));
    } catch (err: any) {
      setError(err.message || "Failed to load API keys");
    } finally {
      setLoading(false);
    }
  }, [signRequest]);

  useEffect(() => {
    fetchKeys();
  }, [fetchKeys]);

  const handleCreate = useCallback(async () => {
    setCreating(true);
    try {
      const turnstileToken = await getTurnstileToken();
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (turnstileToken) {
        headers["x-turnstile-token"] = turnstileToken;
      }
      const req = new Request(`${API_BASE}/api/keys`, {
        method: "POST",
        body: JSON.stringify({ description: newKeyDescription || "API Key" }),
        headers,
      });
      const signed = await signRequest(req);
      const res = await fetch(signed);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || "Could not create API key");
      }
      const json = await res.json();
      setRevealedKey({ id: json.id, raw: json.key });
      setCopied(false);
      setShowCreateForm(false);
      setNewKeyDescription("");
      await fetchKeys();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setCreating(false);
    }
  }, [signRequest, newKeyDescription, fetchKeys]);

  const handleRotate = useCallback(
    async (keyId: string) => {
      setActionLoading(keyId);
      try {
        const req = new Request(`${API_BASE}/api/keys/${keyId}/rotate`, {
          method: "POST",
        });
        const signed = await signRequest(req);
        const res = await fetch(signed);
        if (!res.ok) throw new Error("Could not rotate API key");
        const json = await res.json();
        setRevealedKey({ id: json.id, raw: json.key });
        setCopied(false);
        setConfirmRotate(null);
        await fetchKeys();
      } catch (err: any) {
        setError(err.message);
      } finally {
        setActionLoading(null);
      }
    },
    [signRequest, fetchKeys],
  );

  const handleToggleDisable = useCallback(
    async (keyId: string, isCurrentlyDisabled: boolean) => {
      setActionLoading(keyId);
      try {
        const action = isCurrentlyDisabled ? "enable" : "disable";
        const req = new Request(`${API_BASE}/api/keys/${keyId}/${action}`, {
          method: "PATCH",
        });
        const signed = await signRequest(req);
        const res = await fetch(signed);
        if (!res.ok) throw new Error(`Could not ${action} API key`);
        setConfirmDisable(null);
        await fetchKeys();
      } catch (err: any) {
        setError(err.message);
      } finally {
        setActionLoading(null);
      }
    },
    [signRequest, fetchKeys],
  );

  const handleCopy = useCallback(async () => {
    if (!revealedKey) return;
    await navigator.clipboard.writeText(revealedKey.raw);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [revealedKey]);

  if (loading) {
    return (
      <div className="max-w-3xl">
        <h1 className="font-medium text-2xl pb-3">API Keys</h1>
        <div className="animate-pulse space-y-3">
          <div className="h-16 bg-gray-800 rounded-lg" />
          <div className="h-16 bg-gray-800 rounded-lg" />
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-3xl">
      <div className="flex justify-between items-center pb-4">
        <h1 className="font-medium text-2xl">API Keys</h1>
        <Button onClick={() => setShowCreateForm(true)} disabled={showCreateForm}>
          Create Key
        </Button>
      </div>

      {error && (
        <div className="border border-red-800 rounded-lg p-3 bg-red-950 mb-4">
          <p className="text-red-400 text-sm">{error}</p>
          <button
            onClick={() => setError(null)}
            className="text-xs text-red-500 underline mt-1"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Revealed key banner */}
      {revealedKey && (
        <div className="border border-primary/30 rounded-lg p-4 bg-primary/5 mb-4">
          <p className="text-sm font-medium text-primary mb-2">
            Your new API key (shown only once):
          </p>
          <div className="flex items-center gap-2">
            <code className="flex-1 bg-black/30 px-3 py-2 rounded text-sm font-mono break-all select-all">
              {revealedKey.raw}
            </code>
            <Button variant="outline" onClick={handleCopy} className="shrink-0">
              {copied ? "Copied!" : "Copy"}
            </Button>
          </div>
          <p className="text-xs text-muted-foreground mt-2">
            Save this key now. You won't be able to see it again.
          </p>
          <button
            onClick={() => setRevealedKey(null)}
            className="text-xs text-primary underline mt-2"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Create form */}
      {showCreateForm && (
        <div className="border border-border rounded-lg p-4 mb-4">
          <p className="text-sm font-medium mb-3">Create a new API key</p>
          <div className="flex gap-2">
            <input
              type="text"
              placeholder="Description (optional)"
              value={newKeyDescription}
              onChange={(e) => setNewKeyDescription(e.target.value)}
              className="flex-1 bg-secondary border border-border rounded-md px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
              onKeyDown={(e) => e.key === "Enter" && handleCreate()}
            />
            <Button onClick={handleCreate} disabled={creating}>
              {creating ? "Creating..." : "Create"}
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                setShowCreateForm(false);
                setNewKeyDescription("");
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}

      {/* Keys list */}
      {keys.length === 0 ? (
        <div className="border border-border rounded-lg p-6 text-center text-muted-foreground">
          <p>No API keys yet.</p>
          <p className="text-sm mt-1">Create one to get started.</p>
        </div>
      ) : (
        <div className="border border-border rounded-lg divide-y divide-border">
          {keys.map((key) => (
            <div key={key.id} className="p-4">
              <div className="flex items-center justify-between">
                <div className={`min-w-0 flex-1 ${!key.active ? "opacity-50" : ""}`}>
                  <div className="flex items-center gap-2">
                    <p className="font-medium truncate">{key.description}</p>
                    {!key.active && (
                      <span className="text-xs bg-yellow-900/50 text-yellow-500 px-1.5 py-0.5 rounded">
                        Disabled
                      </span>
                    )}
                  </div>
                  <p className="text-sm text-muted-foreground mt-0.5 font-mono">
                    {key.key_prefix}...
                  </p>
                  {key.created_at && (
                    <p className="text-xs text-muted-foreground mt-0.5">
                      Created {new Date(key.created_at).toLocaleDateString()}
                    </p>
                  )}
                </div>
                <div className="flex gap-2 ml-4 shrink-0">
                  {confirmRotate === key.id ? (
                    <>
                      <span className="text-xs text-muted-foreground self-center mr-1">
                        Rotate?
                      </span>
                      <Button
                        variant="outline"
                        onClick={() => handleRotate(key.id)}
                        disabled={actionLoading === key.id}
                        className="text-xs px-2 py-1"
                      >
                        {actionLoading === key.id ? "..." : "Yes"}
                      </Button>
                      <Button
                        variant="ghost"
                        onClick={() => setConfirmRotate(null)}
                        className="text-xs px-2 py-1"
                      >
                        No
                      </Button>
                    </>
                  ) : confirmDisable === key.id ? (
                    <>
                      <span className="text-xs text-yellow-500 self-center mr-1">
                        Disable?
                      </span>
                      <Button
                        variant="outline"
                        onClick={() => handleToggleDisable(key.id, false)}
                        disabled={actionLoading === key.id}
                        className="text-xs px-2 py-1"
                      >
                        {actionLoading === key.id ? "..." : "Yes"}
                      </Button>
                      <Button
                        variant="ghost"
                        onClick={() => setConfirmDisable(null)}
                        className="text-xs px-2 py-1"
                      >
                        No
                      </Button>
                    </>
                  ) : (
                    <>
                      {key.active && (
                        <Button
                          variant="outline"
                          onClick={() => {
                            setConfirmDisable(null);
                            setConfirmRotate(key.id);
                          }}
                          className="text-xs"
                        >
                          Rotate
                        </Button>
                      )}
                      <Button
                        variant={key.active ? "ghost" : "outline"}
                        onClick={() => {
                          if (key.active) {
                            setConfirmRotate(null);
                            setConfirmDisable(key.id);
                          } else {
                            handleToggleDisable(key.id, true);
                          }
                        }}
                        disabled={actionLoading === key.id}
                        className={key.active
                          ? "text-xs text-muted-foreground hover:text-yellow-500"
                          : "text-xs text-primary"
                        }
                      >
                        {key.active ? "Disable" : "Enable"}
                      </Button>
                    </>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
