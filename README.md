# Chromaforge plug-in marketplace

The list of plug-ins and pixel filters that [Chromaforge](https://github.com/emmertex/chromaforge)
shows under **File ▸ Plug-ins ▸ Plug-in Marketplace…**, and the tools that keep it current.

- **Browse it:** the [index branch](../../tree/index) has the current list as a table
  (`README.md`) and as the file the app reads (`index.json`).
- **List your plug-in:** add one small file, `plugins/<slug>.toml`, in a pull request.
  [CONTRIBUTING.md](CONTRIBUTING.md) has the rules; most pull requests are checked and merged by
  CI with nobody waiting.
- **Release a new version:** nothing to do here. The index is rebuilt every hour from each
  plug-in's newest GitHub release.

## How it works

```
plugins/<slug>.toml  ──►  tools/market.py build  ──►  index branch: index.json  ──►  the app
 (where a plug-in         (hourly, in CI: newest       (generated, never             (fetched each time the
  is released)             release, module read         edited by hand)               marketplace opens)
                           in a sandbox, SHA-256)
```

This repository holds **no plug-in code and no binaries**. A record says where a plug-in is
released; the index says which file is its newest version and what that file's SHA-256 is; the app
downloads the file from the author's own release and refuses it if the hash differs. The app
never pins a version: it always reads the index as it is now.

## Two tiers

| Tier | Who looked | In the app |
|---|---|---|
| **Vetted** | A marketplace maintainer read the source and recorded what the plug-in may ask for. A release that asks for more is held back until it is reviewed again. | Shown by default. |
| **Community** | CI only: the module is well-formed, sandboxed, and says truthfully what it asks for. Nobody vouches for what it does. | Hidden until the user turns on *Show community plug-ins*. |

Every plug-in, of either tier, runs in the app's sandbox and gets no permission the user didn't
allow. [docs/review.md](docs/review.md) says what each tier does and doesn't promise.

## Documents

- [CONTRIBUTING.md](CONTRIBUTING.md): listing a plug-in, the rules, how to get vetted
- [docs/design.md](docs/design.md): why it is built this way
- [docs/records.md](docs/records.md): every field of a record
- [docs/index-format.md](docs/index-format.md): the index the app reads
- [docs/review.md](docs/review.md): the tiers, the review checklist, reporting a plug-in
- [docs/maintaining.md](docs/maintaining.md): repository settings, the workflows, running the tool locally

The tools and documents are MIT-licensed ([LICENSE](LICENSE)). Each plug-in keeps its own licence,
shown in the app.
