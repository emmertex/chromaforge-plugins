# The index

`index.json` on the `index` branch, fetched by the app from

    https://raw.githubusercontent.com/emmertex/chromaforge-plugins/index/index.json

```json
{
  "format": 1,
  "generated": "2026-11-23T04:17:09Z",
  "source": "https://github.com/emmertex/chromaforge-plugins",
  "plugins": [ { …entry… } ]
}
```

`format` only changes for a change an older app can't read; fields are added without changing it,
and the app ignores fields it doesn't know. `generated` is when an entry last changed, not when
the build last ran.

## An entry

| Field | |
|---|---|
| `id` | the module's manifest id; unique in the index |
| `name`, `author` | from the manifest |
| `version` | from the manifest; the app offers an update when it is above the installed version |
| `kind` | `plugin` or `filter` (a pixel filter) |
| `tier` | `vetted` or `community` |
| `summary`, `description` | plain text; the list shows `summary` |
| `tags` | search words from the record |
| `platforms` | where it can be installed: any of `macos`, `windows`, `linux`, `android`, `web` |
| `url` | the `.wasm` to download: `https://`, a GitHub release file |
| `sha256`, `size` | of that file; the app installs nothing whose SHA-256 differs |
| `requires` | host features the module needs, as its manifest names them |
| `permissions` | the permission groups it may ask the user for |
| `network`, `fs`, `programs` | each host, folder and program (by name) behind those groups |
| `stamped` | the file carries its manifest as a section |
| `signer` | the fingerprint of the key that signed the file, or `null` |
| `license` | SPDX text from the manifest or the repository |
| `homepage`, `repository`, `changelog` | `https://` links |
| `released`, `tag` | the release's publish time and tag |
| `record` | the record that lists it (`plugins/<record>.toml`) |
| `review` | vetted entries only: `{by, date}` |

Everything in an entry that comes from a manifest is text the plug-in's author wrote. The app
treats the whole index as untrusted input: capped in size, every field checked, links shown by
host and opened only when clicked.
