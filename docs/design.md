# Design

## Goals

1. **Nobody maintains a version list.** An author releases on GitHub as they already do; the
   marketplace finds the release.
2. **The app always reads the newest list.** No versions pinned in the app, no app release needed
   to list or update a plug-in.
3. **A user can tell how far a plug-in has been looked at**, and chooses how much risk to see.
4. **Compromising this repository shouldn't be enough to run code with a user's permissions.**

## The shape

| Part | Where | Written by |
|---|---|---|
| Records, `plugins/<slug>.toml` | `main` | plug-in authors, by pull request |
| The tool, `tools/market.py` | `main` | maintainers |
| The index, `index.json` + `README.md` | `index` branch | CI only |
| The modules (`.wasm`) | each author's GitHub releases | authors |

The **Index** workflow runs every hour, on every change to `main`, and on request. For each record
it asks GitHub for the repository's releases, picks the newest one that has a module (the highest
version in a tag; a repository that also keeps sample files in releases just has no module there),
downloads each module, and reads it:

- the file is parsed without running it: imports, exports, the stamped manifest, the signature
  (verified, ed25519);
- its manifest export is called in wasmtime with every import trapping, 50 M instructions of fuel
  and a 256 MiB memory cap, because five of the first seven modules listed carry their manifest
  only in code;
- a stamped manifest must equal what the module answers.

It then writes one entry per module, with the release file's URL and SHA-256, and pushes the
result to the `index` branch if anything changed.

The app fetches `index.json` from that branch's raw URL every time the marketplace opens (and once
a day for automatic updates). It downloads a module from the URL in its entry and installs it only
if its SHA-256 is the entry's.

## Why records on `main` and one generated branch, not a branch per plug-in

KiCad's package repository and nixpkgs both came up as models. Branch-per-package suits a
repository that **holds** each package's files and history. Here the files stay with their
authors, and what this repository holds per plug-in is five lines of TOML. So:

- one folder of records is reviewable in one pull request and searchable in one place;
- one generated branch gives the app one URL, one fetch, and an atomic update of the whole list;
- the `index` branch's history is the audit log: every version every user was ever offered, with
  its hash, is a commit.

The two-branch split keeps what people write (`main`) apart from what CI writes (`index`), so a
pull request can never edit the index directly.

## Why not mirror the modules

Mirroring would survive an author deleting a release, but it makes this repository a distributor
of every plug-in's code (and its licence obligations), grows without bound, and adds nothing to
safety: the hash in the index already fixes the bytes. If an author replaces a release file, the
hash stops matching and the app refuses the download until the next hourly build has read the new
file. If an author deletes a release, the entry falls back to what the previous index had until
the record is fixed or removed.

## Trust

- **The sandbox and the user's grants are the safety boundary**, not the marketplace. A plug-in
  installed from here starts with no permissions, like any other, and asks the user for each. A
  pixel filter has no imports at all: it can only compute pixels.
- **The index is not signed.** What it adds is a hash and a tier label. A forged index could offer
  a different module, but that module would still be sandboxed, start with nothing granted, and,
  for a plug-in the user already has, need the same author key or the user's explicit yes (the app
  pins each plug-in's signer on first install).
- **Automatic updates are narrow on purpose.** The app installs one unasked only when the new
  version asks for nothing the installed one didn't, is signed as the installed one is, and
  matches the index's hash. Anything else waits for the user.
- **Vetted is a statement about a version and a permission set.** `[review]` records what was
  read. Later releases inside that set flow through unreviewed; the tool holds any release outside
  it (`outside_review`), keeps the index at the reviewed version, and opens an issue.
- **Community is a statement about form only.** See [review.md](review.md).

## Automatic listing

`listing.yml` runs on `pull_request_target`, so it has a token that can merge. It never checks out
or executes anything from the pull request: tools come from `main`, and the changed records are
fetched through the API as data and parsed as TOML. The rules it applies are in
[CONTRIBUTING.md](../CONTRIBUTING.md). The one that matters most: an existing record can only be
changed automatically by a maintainer **named in the record on `main`**, so nobody can point
someone else's listing at another repository.

Set the repository variable `AUTO_MERGE_COMMUNITY` to `false` to turn automatic merging off; the
check and its comment still run.

## What it doesn't do yet

- Releases outside GitHub. The record format has room (`repo` could become one of several
  sources), the tool doesn't.
- Icons and screenshots in the list. The app draws each entry's icon from its id until it is
  installed.
- Download counts or ratings.
- A signed index.
