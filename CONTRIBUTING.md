# Listing a plug-in

## Before you start

Your plug-in or pixel filter must:

1. **Live in a public GitHub repository with a licence.** Any licence: it is shown in the app.
   Put it in the manifest's `license` or in a `LICENSE` file GitHub recognises.
2. **Be released as a `.wasm` file attached to a GitHub release.** One release may carry several
   modules; each becomes its own entry.
3. **Have a stable `id` and a `version` in its manifest.** A plug-in's version is semver
   (`1.4.0`); a pixel filter's starts with dotted numbers. The marketplace offers an update only
   when the version goes up, so raise it with every release.
4. **Load in the app.** A plug-in imports only `host.call`, `host.response` and `host.log`; a pixel
   filter imports nothing. A stamped manifest must match what the module answers, and a signature
   must be valid ([the app's plug-in guide](https://github.com/emmertex/chromaforge/blob/main/docs/plugins.md)).

Stamping and signing are not required, but do both if you can: the index shows whether a module is
signed, and the app warns before an update signed by a different key.

## Add a record

Create `plugins/<slug>.toml` (the slug: lowercase letters, digits and dashes) in a pull request:

```toml
repo = "your-login/your-plugin"      # where the releases are
maintainers = ["your-login"]         # who may change this record
tags = ["film", "export"]            # up to 8, for search
```

That is a complete record. [docs/records.md](docs/records.md) has the optional fields: a tag or
file-name pattern when one repository releases several things, platforms, a summary, pre-releases.

Check it locally if you like:

```sh
python -m venv .venv && .venv/bin/pip install -r tools/requirements.txt
.venv/bin/python tools/market.py check --only <slug>
```

## What happens to the pull request

CI downloads your newest release, reads each module in a sandbox and comments what it found: the
id, version, kind, platforms, every permission, host, folder and program the module asks for, and
whether it is signed.

**It is merged automatically**, with no maintainer involved, when all of these hold:

- the pull request only adds or changes `plugins/*.toml` files, all of tier `community`;
- every record checks out;
- you are listed in the record's `maintainers`, **and** you own the plug-in's repository or
  published its newest release;
- for a record that already exists: you are one of its maintainers as listed on `main`.

Anything else (removing a record, listing someone else's plug-in, asking for the vetted tier,
changing tools or documents) waits for a maintainer.

Your plug-in appears in the app within the hour, in the community tier.

## Rules

- **One id, one record.** A module id belongs to the record that listed it first. Use an id you
  control (`io.github.<your-login>.<name>` is a good one).
- **Say what it does.** The manifest's description and the permissions it asks for must match what
  the code does. A plug-in that hides what it does is removed.
- **Ask for the least.** Name the hosts, folders and programs you need, no more. Users see the
  whole list before they install.
- **No malware, no tracking users without telling them, no code fetched and run from elsewhere, no
  attempts to leave the sandbox.** Removed on sight, and the author's other records with it.
- **Nothing you may not distribute.** No proprietary profiles, presets, LUTs or look tables you
  don't have the right to ship (Adobe's included).
- **Keep it installable.** A record whose releases stop loading is reported in an issue; one that
  stays broken for 90 days may be removed.

## Getting vetted

Open an issue with the *Request vetting* template once your plug-in has been listed for a while.
A maintainer reads the source of the released version ([docs/review.md](docs/review.md)) and, if it
passes, sets `tier = "vetted"` with a `[review]` table recording what the plug-in may ask for.

After that your releases still reach users automatically, **as long as they ask for nothing new**.
A release that adds a permission, a host, a folder or a program, or is signed by another key, is
held at the reviewed version until a maintainer has looked at it.

## Removing or reporting a plug-in

To withdraw your own plug-in, open a pull request deleting its record. To report one that
misbehaves, use the *Report a plug-in* issue template; for anything that puts users at risk, use
GitHub's private vulnerability reporting on this repository instead of a public issue.
