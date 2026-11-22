# Instructions for agents

This repository is the plug-in marketplace for Chromaforge: records in `plugins/`, one tool in
`tools/market.py`, and a generated `index` branch. Read `docs/design.md` first.

- Never edit the `index` branch by hand, and never commit a `.wasm` file: modules stay in their
  authors' releases.
- A module is untrusted input. Parse it; run only its manifest export, only through
  `market.inspect` (wasmtime, every import trapping, fuel and memory capped). Never execute
  anything else from a plug-in's repository.
- `listing.yml` runs with a write token on pull requests from strangers. It must never check out
  or run pull-request code, and every value from the event goes through `env:`, never straight
  into a script.
- Only a human maintainer sets `tier = "vetted"` or writes `[review]`. Don't promote a record
  because its checks pass.
- Every change to the tool comes with a test in `tests/test_market.py`; hostile inputs go in the
  table in `test_hostile_modules_are_refused_not_run_away_with`.
- The index format is read by released versions of the app: add fields, never rename or remove
  one, and update `docs/index-format.md` in the same commit.
