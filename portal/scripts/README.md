# portal/scripts

## sync-shared.mjs

Pulls the shared name-generator UI from `einiba/canyougrab-site` at a pinned
SHA (see `.shared-source-sha`) and writes it into `portal/src/shared/`.

The portal and the marketing site (`canyougrab.it/find-a-name`) share the
same `<NameGenerator />` component so we don't fork the UX. Source of truth
lives in canyougrab-site; the portal vendors a copy.

### When to run

Run **whenever you bump `.shared-source-sha`** — after a feature has landed
in canyougrab-site that you want to flow into the portal.

```bash
# 1. Update the pinned SHA
echo abc1234 > portal/scripts/.shared-source-sha

# 2. Sync the files
cd portal && npm run sync-shared

# 3. Commit the diff
git add portal/scripts/.shared-source-sha portal/src/shared/
git commit -m "chore(portal): sync shared name-generator to abc1234"

# 4. Push — the Docker build picks up the vendored files
git push
```

### Auth

The script uses `GITHUB_TOKEN` (env var) for GitHub API auth. If the
canyougrab-site repo is private, set:

```bash
export GITHUB_TOKEN=$(gh auth token)
```

Or pass inline: `GITHUB_TOKEN=$(gh auth token) npm run sync-shared`.

### CI auto-sync (in `docker-build-prod.yml`)

Once the `SHARED_SYNC_TOKEN` repo secret is configured, every portal
build automatically re-vendors `src/shared/` from canyougrab-site at the
SHA in `.shared-source-sha` before `docker build` runs. The vendored
copy in the repo becomes a fallback only.

Setup:

1. Create a fine-grained GitHub PAT with **Contents: read** on
   `einiba/canyougrab-site`.
2. Add it to **both** `einiba/canyougrab-api` and
   `ericismaking/canyougrab-api` as the `SHARED_SYNC_TOKEN` secret.

If the secret is absent the CI step logs a notice and skips, so the
existing committed copy is used. After verifying CI works, you can drop
the vendored `portal/src/shared/` from the repo and add it to
`.gitignore` — the workflow will always re-vendor at build time.

### Why we still keep the local script

`npm run sync-shared` remains the right tool when you want to validate
locally before pushing the SHA bump. CI re-runs the same script, so the
behaviour matches.
