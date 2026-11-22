# Tiers and review

## What each tier means

**Community** means the module passed the automatic checks, and nothing more:

- it is a valid WebAssembly module of at most 32 MiB, of one of the two kinds the app runs;
- a plug-in imports only the three host functions; a pixel filter imports nothing;
- its manifest could be read in a sandbox, has a usable id and version, and, if stamped, matches;
- its signature, if any, is valid;
- its repository is public and names a licence;
- the index says truthfully what it may ask for.

Nobody has read its code. It still runs in the app's sandbox with nothing granted, so what it can
do is bounded by what the user allows it. That is why the app hides this tier until the user asks
for it, and says so when they do.

**Vetted** means a marketplace maintainer also did the review below, on a named version, and
wrote down the permissions that version asks for. It is not a guarantee: it is one person's
reading of one version, and later versions inside the same permissions aren't read again.

## What a review looks at

For the release being vetted, at its tag:

1. **Source and binary.** Does the repository build this module? Build it, or check the release
   workflow that did. A release file with no source or build is not vetted.
2. **Permissions against purpose.** Each group, host, folder and program in the manifest: is it
   needed for what the description says? Are hosts specific (not `*`), folders narrow, programs
   real products?
3. **What it sends and where.** For `network`: what leaves the machine (photos, metadata,
   identifiers), to whom, and does the description tell the user?
4. **What it writes.** For `metadataWrite`, `fs`, `libraryAdd`, `crop`: only what the user asked
   for, on the photos they selected?
5. **Dependencies and vendored code.** Anything obfuscated, minified without a source, fetched at
   build time from somewhere unpinned, or licensed incompatibly?
6. **Assets and data.** Look tables, profiles, presets: does the author have the right to ship
   them?
7. **The author.** Stamped and signed? Is the signing key's fingerprint published by the author?
   Does the repository have a history, or did it appear yesterday?

Then set `tier = "vetted"` and write `[review]` ([records.md](records.md)), listing exactly what
the reviewed version asks for and, when it is signed, its signer. Say in `notes` which version and
commit were read.

The optional **AI review** workflow (label a pull request `ai-review`) does a first reading of
points 2 to 5 and comments. It is an aid to the reviewer, never the review.

## When a vetted plug-in changes

- **Same permissions, same signer:** listed automatically. Skim the diff when you can.
- **Anything more, or another signer:** the Index workflow holds the entry at the reviewed version
  and names what is new in its issue. Review the change; then widen `[review]` or leave it held.
- **Lost trust:** set `tier = "community"`, or delete the record.

## Reporting a plug-in

Use the *Report a plug-in* issue template. For anything that endangers users (a module that
exfiltrates data, escapes the sandbox, or lies about what it asks for), report privately through
this repository's **Security ▸ Report a vulnerability** instead.

A maintainer who confirms a report deletes the record; the next index build drops the entry, and
the app stops offering it within the hour. Removing an entry does not uninstall the plug-in from
anyone's computer: say so in the issue, with the id, so users can remove it.
