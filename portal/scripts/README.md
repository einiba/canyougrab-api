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

### Why vendor instead of CI sync

Vendoring keeps Docker builds offline and deterministic — the synced files
are part of the build context. Future improvement: move sync into the
GitHub Actions workflow with a deploy key or PAT secret, so that a portal
build at any SHA also fetches the matching shared SHA. For v1 the manual
flow above is simple and visible in PRs.
