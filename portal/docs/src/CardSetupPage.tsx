import { useState, useEffect, useRef, useCallback } from "react";
import { useAuth, useZudoku } from "zudoku/hooks";
import { Button } from "zudoku/components";
import { API_BASE, STRIPE_PUBLISHABLE_KEY } from "./config.js";

/**
 * Loads Stripe.js from the CDN and returns the Stripe instance.
 * Caches the promise so we only load once.
 */
let stripePromise: Promise<any> | null = null;

function loadStripe(): Promise<any> {
  if (stripePromise) return stripePromise;
  stripePromise = new Promise((resolve, reject) => {
    if ((window as any).Stripe) {
      resolve((window as any).Stripe(STRIPE_PUBLISHABLE_KEY));
      return;
    }
    const script = document.createElement("script");
    script.src = "https://js.stripe.com/v3/";
    script.onload = () => resolve((window as any).Stripe(STRIPE_PUBLISHABLE_KEY));
    script.onerror = () => reject(new Error("Failed to load Stripe.js"));
    document.head.appendChild(script);
  });
  return stripePromise;
}

type Step = "loading" | "form" | "processing" | "success" | "error";

export function CardSetupPage() {
  const auth = useAuth();
  const { signRequest } = useZudoku();
  const [step, setStep] = useState<Step>("loading");
  const [errorMsg, setErrorMsg] = useState("");
  const cardRef = useRef<HTMLDivElement>(null);
  const elementsRef = useRef<any>(null);
  const stripeRef = useRef<any>(null);
  const clientSecretRef = useRef<string>("");

  // Mount Stripe Elements on load
  useEffect(() => {
    if (!auth.isAuthenticated) return;

    const secret = sessionStorage.getItem("setup_intent_secret");
    if (!secret) {
      setErrorMsg("No card setup session found. Please start from the Usage page.");
      setStep("error");
      return;
    }
    clientSecretRef.current = secret;

    loadStripe()
      .then((stripe) => {
        stripeRef.current = stripe;
        const elements = stripe.elements({ clientSecret: secret });
        elementsRef.current = elements;

        const card = elements.create("payment", {
          layout: "tabs",
        });
        if (cardRef.current) {
          card.mount(cardRef.current);
        }
        setStep("form");
      })
      .catch(() => {
        setErrorMsg("Failed to load payment form. Please try again.");
        setStep("error");
      });

    return () => {
      // Cleanup: unmount elements if they exist
      if (elementsRef.current) {
        try {
          elementsRef.current.getElement("payment")?.unmount();
        } catch {
          // ignore
        }
      }
    };
  }, [auth.isAuthenticated]);

  const handleSubmit = useCallback(async () => {
    if (!stripeRef.current || !elementsRef.current) return;

    setStep("processing");
    setErrorMsg("");

    try {
      // Confirm the SetupIntent with Stripe
      const { error, setupIntent } = await stripeRef.current.confirmSetup({
        elements: elementsRef.current,
        confirmParams: {
          return_url: window.location.href, // fallback, won't redirect with redirect:"if_required"
        },
        redirect: "if_required",
      });

      if (error) {
        setErrorMsg(error.message || "Card verification failed. Please try again.");
        setStep("form");
        return;
      }

      if (setupIntent?.status !== "succeeded") {
        setErrorMsg("Card setup did not complete. Please try again.");
        setStep("form");
        return;
      }

      // Card verified — now call backend to confirm Free+ upgrade
      const req = new Request(API_BASE + "/api/billing/confirm-free-plus", {
        method: "POST",
      });
      const signed = await signRequest(req);
      const res = await fetch(signed);

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setErrorMsg(body.detail || "Failed to upgrade plan. Please contact support.");
        setStep("form");
        return;
      }

      // Clean up sessionStorage
      sessionStorage.removeItem("setup_intent_secret");
      setStep("success");
    } catch (err: any) {
      setErrorMsg(err.message || "Something went wrong. Please try again.");
      setStep("form");
    }
  }, [signRequest]);

  if (!auth.isAuthenticated) {
    return (
      <div className="max-w-lg pt-(--padding-content-top) pb-(--padding-content-bottom)">
        <h1 className="font-medium text-2xl pb-3">Add Card</h1>
        <p className="text-muted-foreground">Please sign in to continue.</p>
      </div>
    );
  }

  return (
    <div className="max-w-lg pt-(--padding-content-top) pb-(--padding-content-bottom)">
      <h1 className="font-medium text-2xl pb-3">Add Card on File</h1>

      {step === "loading" && (
        <div className="animate-pulse">
          <div className="h-40 bg-gray-800 rounded-lg" />
        </div>
      )}

      {step === "error" && (
        <div className="border border-red-800 rounded-lg p-4 bg-red-950">
          <p className="text-red-400">{errorMsg}</p>
          <Button onClick={() => (window.location.href = "/usage")} className="mt-3">
            Back to Usage
          </Button>
        </div>
      )}

      {(step === "form" || step === "processing") && (
        <>
          <p className="text-muted-foreground mb-4">
            Add a card to unlock <strong>Free+</strong> — 100 lookups/month,
            100 requests/min, and 25 domains per request. Your card will be
            validated but <strong>not charged</strong>.
          </p>

          <div className="border rounded-lg p-4 mb-4 bg-card">
            <div ref={cardRef} className="min-h-[80px]" />
          </div>

          {errorMsg && (
            <p className="text-red-400 text-sm mb-3">{errorMsg}</p>
          )}

          <div className="flex gap-3">
            <Button
              onClick={handleSubmit}
              disabled={step === "processing"}
              className="flex-1"
            >
              {step === "processing" ? "Verifying..." : "Verify Card & Upgrade"}
            </Button>
            <Button
              variant="outline"
              onClick={() => (window.location.href = "/usage")}
              disabled={step === "processing"}
            >
              Cancel
            </Button>
          </div>

          <p className="text-xs text-muted-foreground mt-3">
            Your card is securely processed by Stripe. We never see or store
            your full card number.
          </p>
        </>
      )}

      {step === "success" && (
        <div className="border border-primary/30 rounded-lg p-6 bg-primary/10 text-center">
          <p className="text-lg font-medium text-primary mb-2">
            You're on Free+!
          </p>
          <p className="text-muted-foreground mb-4">
            Your account has been upgraded to 100 lookups/month, 100
            requests/min, and 25 domains per request.
          </p>
          <Button onClick={() => (window.location.href = "/usage")}>
            View Usage Dashboard
          </Button>
        </div>
      )}
    </div>
  );
}
