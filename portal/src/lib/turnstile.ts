import { TURNSTILE_SITE_KEY } from "@/config";

export async function getTurnstileToken(): Promise<string> {
  const turnstile = (window as any).turnstile;
  if (!turnstile) return "";

  return new Promise<string>((resolve) => {
    const container = document.createElement("div");
    container.style.display = "none";
    document.body.appendChild(container);

    turnstile.render(container, {
      sitekey: TURNSTILE_SITE_KEY,
      callback: (token: string) => {
        resolve(token);
        try {
          turnstile.remove(container);
        } catch {}
        container.remove();
      },
      "error-callback": () => {
        resolve("");
        container.remove();
      },
      "expired-callback": () => {
        resolve("");
        container.remove();
      },
      size: "invisible",
    });
  });
}
