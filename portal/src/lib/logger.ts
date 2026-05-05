type LogLevel = "debug" | "info" | "warn" | "error";

const isDev = import.meta.env.DEV;

function log(level: LogLevel, message: string, data?: unknown) {
  const entry = { ts: new Date().toISOString(), level, message, ...(data ? { data } : {}) };
  switch (level) {
    case "error":
      console.error(`[${entry.ts}] ERROR: ${message}`, data ?? "");
      break;
    case "warn":
      console.warn(`[${entry.ts}] WARN: ${message}`, data ?? "");
      break;
    case "info":
      console.info(`[${entry.ts}] INFO: ${message}`, data ?? "");
      break;
    default:
      if (isDev) console.debug(`[${entry.ts}] DEBUG: ${message}`, data ?? "");
  }
}

export const logger = {
  debug: (msg: string, data?: unknown) => log("debug", msg, data),
  info: (msg: string, data?: unknown) => log("info", msg, data),
  warn: (msg: string, data?: unknown) => log("warn", msg, data),
  error: (msg: string, data?: unknown) => log("error", msg, data),
};
