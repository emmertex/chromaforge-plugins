# Maintaining the marketplace

## Repository settings (once)

- **Settings ▸ Actions ▸ General:** Actions must be allowed to run. Each workflow declares the
  permissions it needs (the Index workflow pushes the `index` branch and files its issue; the
  Listing workflow comments and merges), so the default token setting can stay read-only.
- **Settings ▸ Code security:** turn on *Private vulnerability reporting*.
- **Branch protection on `main`** (recommended): require the `CI / check` status. Don't require
  reviews if you want automatic listing: the Listing workflow merges with the Actions token. With
  required reviews, it comments and a maintainer merges.
- **Protect the `index` branch** (recommended): restrict pushes to GitHub Actions, so the index is
  only ever what the tool wrote.
- **Variable `AUTO_MERGE_COMMUNITY`:** set to `false` to stop automatic merging.
- **Secret `ANTHROPIC_API_KEY`:** optional, enables the `ai-review` label.

## The workflows

| Workflow | Runs | Does |
|---|---|---|
| `ci.yml` | pull requests, pushes to `main` | the tool's tests; checks every record. Read-only token. |
| `index.yml` | hourly, pushes to `main`, by hand | rebuilds the index, pushes `index` if it changed, keeps one issue listing records that fail or are held |
| `listing.yml` | pull requests touching `plugins/` | checks the changed records with `main`'s tools, comments, merges when the rules allow |
| `ai-review.yml` | the `ai-review` label, by hand | optional AI reading of a plug-in's source, as a comment |

A GitHub scheduled workflow is disabled after 60 days without repository activity; any commit, or
*Run workflow* on Index, starts it again.

## Running the tool

```sh
python -m venv .venv && .venv/bin/pip install -r tools/requirements.txt
.venv/bin/python -m unittest discover -s tests          # the tool's tests
.venv/bin/python tools/market.py check                  # every record, as CI does
.venv/bin/python tools/market.py build --out _site      # write _site/index.json
.venv/bin/python tools/market.py inspect some.wasm      # what the index would say about a file
```

Set `GITHUB_TOKEN` (or `GH_TOKEN`) to raise GitHub's rate limit; the tool sends it to
`api.github.com` only.

## Everyday jobs

- **A pull request waits for you** when it isn't a plain community listing by the plug-in's own
  author. Read the Listing comment, then merge or close.
- **Vetting:** [review.md](review.md).
- **The "Index: records that need attention" issue** lists records whose newest release can't be
  read (the entry stays at its last good version) and vetted modules held for review. It closes
  itself.
- **Removing a plug-in:** delete its record. The entry goes with the next build.
- **Testing the app against a draft index:** start the app with `<PREFIX>_MARKETPLACE` set to
  another `https://` index URL (for example a fork's `index` branch).
