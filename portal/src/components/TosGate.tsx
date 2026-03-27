import { ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { useSignRequest } from "@/hooks/useSignRequest";
import { API_BASE } from "@/config";

const CURRENT_TOS_VERSION = "1.0";

interface TosGateProps {
  children: ReactNode;
  tosAcceptedAt: string | null;
  tosVersion: string | null;
  onAccepted: () => void;
}

export function TosGate({ children, tosAcceptedAt, tosVersion, onAccepted }: TosGateProps) {
  const [accepting, setAccepting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { signRequest } = useSignRequest();
  const scrollRef = useRef<HTMLDivElement>(null);
  const [scrolledToBottom, setScrolledToBottom] = useState(false);

  // If TOS already accepted at current version, render children
  if (tosAcceptedAt && tosVersion === CURRENT_TOS_VERSION) {
    return <>{children}</>;
  }

  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    if (atBottom) setScrolledToBottom(true);
  }, []);

  const handleAccept = async () => {
    setAccepting(true);
    setError(null);
    try {
      const req = await signRequest(
        new Request(`${API_BASE}/api/auth/accept-tos`, { method: "POST" }),
      );
      const resp = await fetch(req);
      if (!resp.ok) {
        throw new Error("Failed to accept terms");
      }
      onAccepted();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setAccepting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <div className="bg-background border border-border rounded-xl shadow-2xl max-w-lg w-full mx-4 flex flex-col max-h-[90vh]">
        <div className="px-6 pt-6 pb-4">
          <h2 className="text-lg font-semibold text-foreground">Terms of Service</h2>
          <p className="text-sm text-muted-foreground mt-1">
            {tosVersion && tosVersion !== CURRENT_TOS_VERSION
              ? "Our Terms of Service have been updated. Please review and accept the new terms to continue."
              : "Please review and accept our Terms of Service to continue using CanYouGrab."}
          </p>
        </div>

        <div
          ref={scrollRef}
          onScroll={handleScroll}
          className="px-6 overflow-y-auto flex-1 border-t border-b border-border"
        >
          <div className="py-4 text-sm text-muted-foreground space-y-3 leading-relaxed">
            <p>By accepting, you agree to:</p>
            <ul className="list-disc pl-5 space-y-2">
              <li>
                Use the CanYouGrab API only for lawful purposes related to domain name
                registration.
              </li>
              <li>
                Not compile, harvest, or build databases of domain registration data for
                resale or redistribution.
              </li>
              <li>
                Not use data obtained through the Service for unsolicited communications
                or marketing.
              </li>
              <li>
                Comply with the acceptable use policies of all upstream registry operators
                (Verisign, PIR, DENIC, and others) whose data you access through the
                Service.
              </li>
              <li>
                Abide by the rate limits of your plan and not attempt to circumvent access
                controls.
              </li>
            </ul>
            <p className="pt-2">
              Read the full terms at{" "}
              <Link
                to="/terms"
                target="_blank"
                className="text-primary hover:underline"
              >
                Terms of Service
              </Link>
              , including the complete list of third-party data provider policies.
            </p>
          </div>
        </div>

        <div className="px-6 py-4 space-y-3">
          {error && (
            <div className="text-sm text-red-400 bg-red-400/10 border border-red-400/20 rounded px-3 py-2">
              {error}
            </div>
          )}
          <button
            onClick={handleAccept}
            disabled={accepting}
            className="w-full py-2.5 rounded-lg bg-primary text-white text-sm font-medium hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {accepting ? "Accepting..." : "I have read and agree to the Terms of Service"}
          </button>
          <p className="text-xs text-muted-foreground text-center">
            Version {CURRENT_TOS_VERSION}
          </p>
        </div>
      </div>
    </div>
  );
}
