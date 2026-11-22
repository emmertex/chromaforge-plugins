# Records

A record is `plugins/<slug>.toml`. The slug is the file name: 1 to 63 lowercase letters, digits
and dashes. It names the record, not the plug-in; the plug-in's id comes from its manifest.

```toml
repo = "owner/name"                 # required: the GitHub repository with the releases
maintainers = ["login"]             # required: 1 to 8 GitHub logins who may change this record

tier = "community"                  # "community" (default) or "vetted" (maintainers only)
assets = "*.wasm"                   # which release files are modules (a file-name pattern)
tag = "*"                           # which release tags count (a pattern, e.g. "myplugin-v*")
prerelease = false                  # true: pre-releases count too

platforms = ["macos", "windows"]    # only to override what the module itself says (below)
tags = ["film", "look"]             # up to 8 search words: lowercase letters, digits, dashes
summary = "One line for the list."  # default: the first sentence of the manifest's description
description = "Longer text."        # default: the manifest's description
```

## Which release is listed

The newest release that isn't a draft (or a pre-release, unless `prerelease = true`), whose tag
matches `tag`, and that has at least one file matching `assets`. "Newest" is the highest version
found in a tag (`v1.4.0`, `myplugin-v1.4.0`); tags without a version sort by publish date, after
those with one.

Use `tag` and `assets` when one repository releases several things on separate tags: one record
per thing, each with its own patterns.

## Platforms

Left out, `platforms` is worked out from the module:

- a pixel filter, or a plug-in that starts no programs: every platform the app runs plug-ins on
  (`macos`, `windows`, `linux`, `android`);
- a plug-in that starts programs: the systems its program entries name (`macBundleIds` → macOS,
  `windowsExe` → Windows, `linuxExe` → Linux), never Android, where the app starts no programs.

`web` is a platform a record may name, for the day the web build runs plug-ins; it has none today,
so nothing is listed for it by default.

Set it only when that is wrong, for example a plug-in that depends on a service available in one
place. The app shows every platform as supported or not, and won't install an entry on a platform
it doesn't list.

## Several modules in one release

Each matching file is one entry. To change or leave out one of them:

```toml
[modules."io.github.you.second"]
summary = "A different line for this one."
tags = ["export"]
platforms = ["linux"]
hidden = true          # don't list this module
```

## Vetted records

Only a marketplace maintainer sets these ([review.md](review.md)):

```toml
tier = "vetted"

[review]
by = "maintainer-login"
date = 2026-11-23
permissions = ["catalog", "network"]   # the permission groups the reviewed version asks for
network = ["api.example.com"]          # each host
fs = []                                # each folder
programs = ["converter"]               # each program id
signer = "3f2a 9c41 07be d5e8"         # the author key's fingerprint, when the module is signed
notes = "Read v1.4.0 at commit abc1234."
```

A later release that stays inside this is listed as it comes. One that goes outside it is held:
the index keeps the last version that fit, and the Index workflow says so in its issue.
