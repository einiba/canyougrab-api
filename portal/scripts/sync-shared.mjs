#!/usr/bin/env node
/**
 * sync-shared.mjs — pulls the latest canyougrab-site/src/shared directory
 * at a pinned SHA and writes it into portal/src/shared.
 *
 * Why: the portal and the marketing site share the name-generation UI.
 * Source of truth lives in canyougrab-site. To keep portal builds
 * deterministic, we pin to a specific commit SHA in
 * `portal/scripts/.shared-source-sha`. Bump that file to upgrade.
 *
 * Mechanism: GitHub's contents API + raw.githubusercontent.com over HTTPS.
 * Works in plain Node 20+ (built-in fetch), no extra deps, no git binary
 * needed. Optional GITHUB_TOKEN env var lets it work for private repos
 * and avoids the 60-req/h unauthenticated rate limit.
 *
 * Usage: `node scripts/sync-shared.mjs` (runs automatically as `prebuild`).
 */

import { readFileSync, mkdirSync, writeFileSync, rmSync, existsSync } from "node:fs";
import { dirname, resolve, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const PORTAL_ROOT = resolve(__dirname, "..");
const SHA_FILE = join(__dirname, ".shared-source-sha");
const DEST = join(PORTAL_ROOT, "src", "shared");
const REPO = "einiba/canyougrab-site";
const SRC_PATH = "src/shared"; // copy this directory tree

if (!existsSync(SHA_FILE)) {
  console.error(`sync-shared: ${SHA_FILE} is missing — cannot sync.`);
  process.exit(1);
}

const SHA = readFileSync(SHA_FILE, "utf8").trim();
if (!/^[0-9a-f]{7,40}$/i.test(SHA)) {
  console.error(`sync-shared: invalid SHA in ${SHA_FILE}: ${SHA}`);
  process.exit(1);
}

const HEADERS = {
  Accept: "application/vnd.github+json",
  "User-Agent": "canyougrab-portal-sync",
  ...(process.env.GITHUB_TOKEN
    ? { Authorization: `Bearer ${process.env.GITHUB_TOKEN}` }
    : {}),
};

async function fetchJson(url) {
  const res = await fetch(url, { headers: HEADERS });
  if (!res.ok) {
    throw new Error(`GET ${url} -> ${res.status} ${res.statusText}`);
  }
  return res.json();
}

async function fetchText(url) {
  const res = await fetch(url, { headers: HEADERS });
  if (!res.ok) {
    throw new Error(`GET ${url} -> ${res.status} ${res.statusText}`);
  }
  return res.text();
}

async function syncDir(remotePath, localPath) {
  const url = `https://api.github.com/repos/${REPO}/contents/${remotePath}?ref=${SHA}`;
  const items = await fetchJson(url);
  if (!Array.isArray(items)) {
    throw new Error(`Expected array at ${remotePath}, got ${typeof items}`);
  }

  for (const item of items) {
    const dest = join(localPath, item.name);
    if (item.type === "dir") {
      mkdirSync(dest, { recursive: true });
      await syncDir(item.path, dest);
    } else if (item.type === "file") {
      const text = await fetchText(item.download_url);
      mkdirSync(dirname(dest), { recursive: true });
      writeFileSync(dest, text);
    } else {
      console.warn(`sync-shared: skipping ${item.path} (type=${item.type})`);
    }
  }
}

console.log(`sync-shared: syncing ${REPO}@${SHA} -> ${DEST}`);
const start = Date.now();

if (existsSync(DEST)) {
  rmSync(DEST, { recursive: true, force: true });
}
mkdirSync(DEST, { recursive: true });

try {
  await syncDir(SRC_PATH, DEST);
} catch (err) {
  console.error(`sync-shared: failed: ${err.message}`);
  console.error(
    "Hint: if this is a private repo, set GITHUB_TOKEN with `repo` scope.",
  );
  process.exit(1);
}

const ms = Date.now() - start;
console.log(`sync-shared: done in ${ms}ms`);
