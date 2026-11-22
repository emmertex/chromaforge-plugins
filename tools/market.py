#!/usr/bin/env python3
"""The marketplace's one tool: reads the records in `plugins/`, finds each plug-in's newest
release, inspects every module in a sandbox, and writes the index the app reads.

    market.py build   --out DIR [--previous index.json] [--cache DIR]
    market.py check   [--records DIR] [--only slug ...] [--author LOGIN] [--report FILE]
    market.py inspect FILE.wasm

Nothing a module does runs outside WebAssembly: its manifest export is called in wasmtime with
every import trapping, a fuel budget and a memory cap. See docs/design.md.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import fnmatch
import hashlib
import json
import os
import re
import sys
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

FORMAT = 1
ROOT = Path(__file__).resolve().parent.parent
TIERS = ("vetted", "community")
PLATFORMS = ("macos", "windows", "linux", "android", "web")
# where the app runs plug-ins today: the web build has none, so nothing is listed for it unless
# a record says so
NATIVE = PLATFORMS[:4]
GROUPS = ("catalog", "metadataWrite", "libraryAdd", "pixels", "crop", "virtualCopy", "network", "fs", "programs")
FLAG_GROUPS = GROUPS[:6]
PROGRAM_PLATFORMS = (("macBundleIds", "macos"), ("windowsExe", "windows"), ("linuxExe", "linux"))

MAX_MODULE_BYTES = 32 << 20  # the app's install limit
MAX_PLUGIN_MANIFEST = 64 * 1024
MAX_FILTER_MANIFEST = 1024 * 1024
MAX_SIGNATURE_BYTES = 1024
MAX_SUMMARY = 200
MAX_DESCRIPTION = 2000
FUEL = 50_000_000
MEMORY_CAP = 256 << 20
MANIFEST_SECTION = "photo-plugin.manifest"
SIGNATURE_SECTION = "photo-plugin.signature"

ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$")
LOGIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$")
TAG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,23}$")
# https://semver.org, as the app's `semver` crate reads a plug-in's version
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")

PLUGIN_EXPORTS = ("memory", "lcc_abi_version", "lcc_manifest", "lcc_alloc", "lcc_handle")
FILTER_EXPORTS = ("memory", "pc_abi_version", "pc_manifest", "pc_alloc", "pc_filter")
HOST_IMPORTS = {("host", "call"), ("host", "response"), ("host", "log")}


class Refused(Exception):
    """Why a record, a release or a module can't be listed."""


# ───────────────────────────── WebAssembly, read without running ─────────────────────────────


def _leb(b: bytes, i: int) -> tuple[int, int]:
    value = shift = 0
    for _ in range(5):
        if i >= len(b):
            raise Refused("the module ends inside a number")
        byte = b[i]
        i += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return value, i
    raise Refused("the module has a number longer than 32 bits")


def _name(b: bytes, i: int) -> tuple[str, int]:
    n, i = _leb(b, i)
    if i + n > len(b):
        raise Refused("the module ends inside a name")
    try:
        return b[i : i + n].decode("utf-8"), i + n
    except UnicodeDecodeError:
        raise Refused("the module has a name that isn't UTF-8") from None


@dataclass
class Section:
    id: int
    start: int  # of the section's id byte
    body: bytes
    name: str | None = None  # custom sections
    data: bytes = b""  # a custom section's bytes after its name


def sections(wasm: bytes) -> list[Section]:
    if wasm[:8] != b"\0asm\x01\0\0\0":
        raise Refused("not a WebAssembly module (version 1)")
    out, i = [], 8
    while i < len(wasm):
        start, sid = i, wasm[i]
        size, j = _leb(wasm, i + 1)
        if j + size > len(wasm):
            raise Refused("the module ends inside a section")
        body = wasm[j : j + size]
        s = Section(sid, start, body)
        if sid == 0:
            s.name, k = _name(body, 0)
            s.data = body[k:]
        out.append(s)
        i = j + size
    return out


def _imports(body: bytes) -> list[tuple[str, str, int]]:
    count, i = _leb(body, 0)
    out = []
    for _ in range(count):
        module, i = _name(body, i)
        name, i = _name(body, i)
        if i >= len(body):
            raise Refused("the module ends inside its imports")
        kind = body[i]
        i += 1
        if kind == 0:  # function: a type index
            _, i = _leb(body, i)
        elif kind == 1:  # table: reftype, limits
            i += 1
            i = _limits(body, i)
        elif kind == 2:  # memory: limits
            i = _limits(body, i)
        elif kind == 3:  # global: valtype, mutability
            i += 2
        else:
            raise Refused(f"the module imports something of an unknown kind ({kind})")
        out.append((module, name, kind))
    return out


def _limits(body: bytes, i: int) -> int:
    if i >= len(body):
        raise Refused("the module ends inside its imports")
    flags = body[i]
    _, i = _leb(body, i + 1)
    if flags & 1:
        _, i = _leb(body, i)
    return i


def _exports(body: bytes) -> list[str]:
    count, i = _leb(body, 0)
    out = []
    for _ in range(count):
        name, i = _name(body, i)
        _, i = _leb(body, i + 1)
        out.append(name)
    return out


# ───────────────────────────── a module, inspected ─────────────────────────────


@dataclass
class Module:
    kind: str  # "plugin" | "filter"
    manifest: dict
    sha256: str
    size: int
    stamped: bool
    signer: str | None  # the author key's fingerprint


def fingerprint(key: bytes) -> str:
    digest = hashlib.sha256(key).hexdigest()[:16]
    return " ".join(digest[i : i + 4] for i in range(0, 16, 4))


def _signature(wasm: bytes, secs: list[Section]) -> str | None:
    """The fingerprint of the key that signed the file, None when it's unsigned; an invalid
    signature is never "unsigned"."""
    signed = [s for s in secs if s.name == SIGNATURE_SECTION]
    if not signed:
        return None
    if len(signed) > 1:
        raise Refused("the file has more than one signature section")
    s = signed[0]
    if s is not secs[-1]:
        raise Refused("the signature section isn't the file's last section")
    if len(s.data) > MAX_SIGNATURE_BYTES:
        raise Refused("the signature section is over 1 KiB")
    try:
        doc = json.loads(s.data.decode("utf-8"))
        if not isinstance(doc, dict) or doc.get("v") != 1:
            raise ValueError("format version")
        key = base64.b64decode(doc["key"], validate=True)
        sig = base64.b64decode(doc["sig"], validate=True)
    except (ValueError, KeyError, TypeError, UnicodeDecodeError) as e:
        raise Refused(f"the signature section can't be read ({e})") from None
    if len(key) != 32 or len(sig) != 64:
        raise Refused("the signature's key or signature has the wrong length")
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        Ed25519PublicKey.from_public_bytes(key).verify(sig, wasm[: s.start])
    except InvalidSignature:
        raise Refused("the signature doesn't match the file") from None
    return fingerprint(key)


def _run_manifest(wasm: bytes, prefix: str, cap: int) -> bytes:
    """What `<prefix>_manifest` returns, the module run in a sandbox: every import traps."""
    import wasmtime

    config = wasmtime.Config()
    config.consume_fuel = True
    engine = wasmtime.Engine(config)
    try:
        module = wasmtime.Module(engine, wasm)
        store = wasmtime.Store(engine)
        store.set_fuel(FUEL)
        store.set_limits(memory_size=MEMORY_CAP, instances=1, memories=1)
        linker = wasmtime.Linker(engine)

        def trap(*_args):
            raise RuntimeError("the manifest export called the host")

        for imp in module.imports:
            linker.define(store, imp.module, imp.name, wasmtime.Func(store, imp.type, trap))
        exports = linker.instantiate(store, module).exports(store)
        version = exports[f"{prefix}_abi_version"](store)
        if version != 1:
            raise Refused(f"ABI version {version} (this marketplace lists version 1)")
        packed = exports[f"{prefix}_manifest"](store) & 0xFFFF_FFFF_FFFF_FFFF
        ptr, size = packed & 0xFFFF_FFFF, packed >> 32
        if size == 0 or size > cap:
            raise Refused(f"the manifest is {size} bytes (1 to {cap})")
        memory = exports["memory"]
        if ptr + size > memory.data_len(store):
            raise Refused("the manifest points outside the module's memory")
        return bytes(memory.read(store, ptr, ptr + size))
    except Refused:
        raise
    except Exception as e:  # wasmtime's errors and traps, a missing or mistyped export
        raise Refused(f"the module can't answer for its manifest: {str(e).splitlines()[0][:300]}") from None


def inspect(wasm: bytes) -> Module:
    if len(wasm) > MAX_MODULE_BYTES:
        raise Refused(f"the module is {len(wasm)} bytes (limit {MAX_MODULE_BYTES})")
    secs = sections(wasm)
    imports = [i for s in secs if s.id == 2 for i in _imports(s.body)]
    exports = {e for s in secs if s.id == 7 for e in _exports(s.body)}
    if all(e in exports for e in FILTER_EXPORTS):
        kind, prefix, cap = "filter", "pc", MAX_FILTER_MANIFEST
        if imports:
            raise Refused(f"a pixel filter imports nothing, this one imports {imports[0][0]}.{imports[0][1]}")
    elif all(e in exports for e in PLUGIN_EXPORTS):
        kind, prefix, cap = "plugin", "lcc", MAX_PLUGIN_MANIFEST
        for module, name, what in imports:
            if what != 0 or (module, name) not in HOST_IMPORTS:
                raise Refused(f"a plug-in imports only host.call, host.response and host.log, this one imports {module}.{name}")
    else:
        raise Refused("neither a plug-in (lcc_* exports) nor a pixel filter (pc_* exports)")
    signer = _signature(wasm, secs)
    raw = _run_manifest(wasm, prefix, cap)
    stamps = [s for s in secs if s.name == MANIFEST_SECTION]
    if len(stamps) > 1:
        raise Refused("the file has more than one manifest section")
    if stamps and stamps[0].data != raw:
        raise Refused("the stamped manifest differs from what the module answers (stamp it again)")
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise Refused("the manifest isn't UTF-8 JSON") from None
    if not isinstance(manifest, dict):
        raise Refused("the manifest isn't a JSON object")
    _check_manifest(kind, manifest)
    return Module(kind, manifest, hashlib.sha256(wasm).hexdigest(), len(wasm), bool(stamps), signer)


def _text(v, cap: int) -> str:
    """Plain one-line text: white space folded, control and invisible characters gone."""
    if not isinstance(v, str):
        return ""
    hidden = lambda c: ord(c) < 32 or c in "​‌‍‎‏‪‫‬‭‮⁦⁧⁨⁩﻿"
    words = ["".join(c for c in w if not hidden(c)) for w in v.split()]
    text = " ".join(w for w in words if w)
    return text if len(text) <= cap else text[: cap - 1].rstrip() + "…"


def _summary(description: str) -> str:
    """A description's first sentence, for a list row."""
    m = re.match(r"^(.{20,}?[.!?])(\s|$)", description)
    first = m[1] if m else description
    if first.endswith("."):
        first = first[:-1]
    return _text(first, MAX_SUMMARY)


def _check_manifest(kind: str, m: dict) -> None:
    if not isinstance(m.get("id"), str) or not ID_RE.match(m["id"]):
        raise Refused("the manifest's `id` isn't 1 to 64 of A-Z a-z 0-9 . _ -")
    if not _text(m.get("name"), 100):
        raise Refused("the manifest has no `name`")
    version = m.get("version")
    if not isinstance(version, str) or not version.strip() or len(version) > 64:
        raise Refused("the manifest has no `version`")
    if kind == "plugin" and not SEMVER_RE.match(version.strip()):
        raise Refused(f"the version {version!r} isn't semver (MAJOR.MINOR.PATCH)")
    if kind == "filter" and not re.match(r"^\d+(\.\d+)*([-+.][0-9A-Za-z.+-]*)?$", version.strip()):
        raise Refused(f"the version {version!r} doesn't start with dotted numbers")
    perms = m.get("permissions", {})
    if kind == "plugin" and not isinstance(perms, dict):
        raise Refused("the manifest's `permissions` isn't an object")


def permissions(m: Module) -> dict:
    """What the module may ask the user for: `groups` plus each host, folder and program."""
    if m.kind != "plugin":
        return {"groups": [], "network": [], "fs": [], "programs": []}
    p = m.manifest.get("permissions") or {}
    network = sorted({h.lower() for h in p.get("network") or [] if isinstance(h, str)})
    fs = sorted({r["path"] for r in p.get("fs") or [] if isinstance(r, dict) and isinstance(r.get("path"), str)})
    programs = [r for r in p.get("programs") or [] if isinstance(r, dict) and isinstance(r.get("id"), str)]
    groups = [g for g in FLAG_GROUPS if p.get(g) is True]
    groups += [g for g, has in (("network", network), ("fs", fs), ("programs", programs)) if has]
    return {"groups": groups, "network": network, "fs": fs, "programs": programs}


def platforms(m: Module) -> list[str]:
    """Where the module is of use: everywhere the app runs plug-ins, unless all it does goes
    through programs it starts, which exist on the systems its entries name (never on Android)."""
    programs = permissions(m)["programs"]
    if not programs:
        return list(NATIVE)
    named = {platform for p in programs for key, platform in PROGRAM_PLATFORMS if p.get(key)}
    return [p for p in PLATFORMS if p in named]


# ───────────────────────────── records ─────────────────────────────


@dataclass
class Record:
    slug: str
    repo: str
    maintainers: list[str]
    tier: str = "community"
    assets: str = "*.wasm"
    tag: str = "*"
    prerelease: bool = False
    platforms: list[str] | None = None
    tags: list[str] = field(default_factory=list)
    summary: str = ""
    description: str = ""
    review: dict = field(default_factory=dict)
    modules: dict[str, dict] = field(default_factory=dict)


RECORD_KEYS = {"repo", "maintainers", "tier", "assets", "tag", "prerelease", "platforms", "tags", "summary", "description", "review", "modules"}
REVIEW_KEYS = {"by", "date", "permissions", "network", "fs", "programs", "signer", "notes"}
MODULE_KEYS = {"platforms", "tags", "summary", "description", "hidden"}


def _strings(v, what: str, ok=lambda s: True, most: int = 64) -> list[str]:
    if not isinstance(v, list) or len(v) > most or not all(isinstance(s, str) and ok(s) for s in v):
        raise Refused(f"`{what}` isn't a list of at most {most} valid names")
    return list(v)


def _platform_list(v, what: str) -> list[str]:
    out = _strings(v, what, lambda s: s in PLATFORMS, len(PLATFORMS))
    if not out:
        raise Refused(f"`{what}` is empty: leave it out for the platforms the module itself names")
    return [p for p in PLATFORMS if p in out]


def read_record(path: Path) -> Record:
    slug = path.stem
    if path.suffix != ".toml" or not SLUG_RE.match(slug):
        raise Refused("a record is plugins/<slug>.toml, the slug 1 to 63 of a-z 0-9 -")
    try:
        doc = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as e:
        raise Refused(f"can't be read as TOML: {e}") from None
    unknown = set(doc) - RECORD_KEYS
    if unknown:
        raise Refused(f"unknown keys: {', '.join(sorted(unknown))}")
    repo = doc.get("repo")
    if not isinstance(repo, str) or not REPO_RE.match(repo):
        raise Refused("`repo` isn't a GitHub repository, \"owner/name\"")
    maintainers = _strings(doc.get("maintainers"), "maintainers", lambda s: bool(LOGIN_RE.match(s)), 8)
    if not maintainers:
        raise Refused("`maintainers` names nobody: list the GitHub logins that may change this record")
    r = Record(slug, repo, maintainers)
    r.tier = doc.get("tier", "community")
    if r.tier not in TIERS:
        raise Refused(f"`tier` is {r.tier!r}: one of {', '.join(TIERS)}")
    for key in ("assets", "tag"):
        v = doc.get(key, getattr(r, key))
        if not isinstance(v, str) or not v or len(v) > 100 or "/" in v:
            raise Refused(f"`{key}` isn't a file-name pattern")
        setattr(r, key, v)
    if not r.assets.endswith(".wasm"):
        raise Refused("`assets` must end in .wasm")
    r.prerelease = doc.get("prerelease", False)
    if not isinstance(r.prerelease, bool):
        raise Refused("`prerelease` isn't true or false")
    if "platforms" in doc:
        r.platforms = _platform_list(doc["platforms"], "platforms")
    r.tags = _strings(doc.get("tags", []), "tags", lambda s: bool(TAG_RE.match(s)), 8)
    r.summary = _text(doc.get("summary", ""), MAX_SUMMARY)
    r.description = _text(doc.get("description", ""), MAX_DESCRIPTION)
    review = doc.get("review", {})
    if not isinstance(review, dict) or set(review) - REVIEW_KEYS:
        raise Refused(f"`review` holds only {', '.join(sorted(REVIEW_KEYS))}")
    if r.tier == "vetted":
        if not isinstance(review.get("by"), str) or not LOGIN_RE.match(review["by"]):
            raise Refused("a vetted record needs `review.by`, the reviewing maintainer's GitHub login")
        if not isinstance(review.get("date"), dt.date):
            raise Refused("a vetted record needs `review.date` (YYYY-MM-DD)")
    r.review = {
        "by": review.get("by", ""),
        "date": review["date"].isoformat() if isinstance(review.get("date"), dt.date) else "",
        "permissions": _strings(review.get("permissions", []), "review.permissions", lambda s: s in GROUPS),
        "network": [h.lower() for h in _strings(review.get("network", []), "review.network", lambda s: bool(HOST_RE.match(s.lstrip("*."))))],
        "fs": _strings(review.get("fs", []), "review.fs"),
        "programs": _strings(review.get("programs", []), "review.programs", lambda s: bool(ID_RE.match(s))),
        "signer": " ".join(str(review.get("signer", "")).lower().split()),
    }
    modules = doc.get("modules", {})
    if not isinstance(modules, dict) or len(modules) > 64:
        raise Refused("`modules` isn't a table of at most 64 module ids")
    for mid, over in modules.items():
        if not ID_RE.match(mid) or not isinstance(over, dict) or set(over) - MODULE_KEYS:
            raise Refused(f"`modules.{mid}` holds only {', '.join(sorted(MODULE_KEYS))}")
        o: dict = {"hidden": over.get("hidden", False) is True}
        if "platforms" in over:
            o["platforms"] = _platform_list(over["platforms"], f"modules.{mid}.platforms")
        if "tags" in over:
            o["tags"] = _strings(over["tags"], f"modules.{mid}.tags", lambda s: bool(TAG_RE.match(s)), 8)
        for key, cap in (("summary", MAX_SUMMARY), ("description", MAX_DESCRIPTION)):
            if key in over:
                o[key] = _text(over[key], cap)
        r.modules[mid] = o
    return r


def read_records(folder: Path, only: list[str] | None = None) -> tuple[list[Record], dict[str, str]]:
    records, errors = [], {}
    for path in sorted(folder.glob("*.toml")):
        if only and path.stem not in only:
            continue
        try:
            records.append(read_record(path))
        except Refused as e:
            errors[path.stem] = str(e)
    return records, errors


# ───────────────────────────── GitHub ─────────────────────────────


class GitHub:
    """Releases and their files, read through the public API (a token only raises the rate limit)."""

    def __init__(self, cache: Path | None = None):
        self.token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
        self.cache = cache

    def _open(self, url: str, accept: str):
        req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "plugin-marketplace-index", "X-GitHub-Api-Version": "2022-11-28"})
        # the token goes to the API only, never to where a download redirects
        if self.token and url.startswith("https://api.github.com/"):
            req.add_header("Authorization", f"Bearer {self.token}")
        return urllib.request.urlopen(req, timeout=60)

    def _json(self, path: str):
        try:
            with self._open(f"https://api.github.com/{path}", "application/vnd.github+json") as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            raise Refused(f"GitHub answered {e.code} for {path}") from None
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            raise Refused(f"GitHub can't be reached for {path}: {e}") from None

    def repo(self, repo: str) -> dict:
        return self._json(f"repos/{repo}")

    def releases(self, repo: str) -> list[dict]:
        return self._json(f"repos/{repo}/releases?per_page=100")

    def download(self, asset: dict) -> bytes:
        size = asset.get("size", 0)
        if not isinstance(size, int) or size <= 0 or size > MAX_MODULE_BYTES:
            raise Refused(f"{asset.get('name')} is {size} bytes (1 to {MAX_MODULE_BYTES})")
        stamp = re.sub(r"[^0-9A-Za-z]", "", str(asset.get("updated_at", "")))
        cached = self.cache / f"{asset.get('id')}-{stamp}.wasm" if self.cache else None
        if cached and cached.is_file():
            return cached.read_bytes()
        url = asset.get("browser_download_url", "")
        if not isinstance(url, str) or not url.startswith("https://github.com/"):
            raise Refused(f"{asset.get('name')} has no github.com download link")
        try:
            with self._open(url, "application/octet-stream") as r:
                data = r.read(MAX_MODULE_BYTES + 1)
        except (urllib.error.URLError, TimeoutError) as e:
            raise Refused(f"{asset.get('name')} can't be downloaded: {e}") from None
        if len(data) != size:
            raise Refused(f"{asset.get('name')} is {len(data)} bytes, its release says {size}")
        if cached:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(data)
        return data


def _tag_version(tag: str) -> tuple | None:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)(-[0-9A-Za-z.-]+)?", tag)
    if not m:
        return None
    # a release sorts after its pre-releases
    return (int(m[1]), int(m[2]), int(m[3]), m[4] is None, m[4] or "")


def pick_release(record: Record, releases: list[dict]) -> tuple[dict, list[dict]]:
    """The newest release that has modules: the highest version in a tag, else the latest
    published."""
    best = None
    for rel in releases:
        if not isinstance(rel, dict) or rel.get("draft") or (rel.get("prerelease") and not record.prerelease):
            continue
        tag = str(rel.get("tag_name", ""))
        if not fnmatch.fnmatchcase(tag, record.tag):
            continue
        assets = [a for a in rel.get("assets") or [] if isinstance(a, dict) and fnmatch.fnmatchcase(str(a.get("name", "")), record.assets)]
        if not assets:
            continue
        version = _tag_version(tag)
        key = (version is not None, version or (), str(rel.get("published_at", "")))
        if best is None or key > best[0]:
            best = (key, rel, sorted(assets, key=lambda a: str(a.get("name"))))
    if best is None:
        raise Refused(f"no release of {record.repo} has a file matching {record.assets!r}" + ("" if record.tag == "*" else f" under a tag matching {record.tag!r}"))
    return best[1], best[2]


# ───────────────────────────── entries ─────────────────────────────


def _https(v) -> str | None:
    ok = isinstance(v, str) and len(v) <= 2048 and re.match(r"^https://[A-Za-z0-9.-]+(:\d+)?(/[^\s]*)?$", v)
    return v if ok else None


def outside_review(record: Record, m: Module) -> list[str]:
    """What a vetted record's module asks for, or is signed by, beyond what was reviewed."""
    if record.tier != "vetted":
        return []
    rv, p, out = record.review, permissions(m), []
    out += [f"permission `{g}`" for g in p["groups"] if g not in rv["permissions"]]
    out += [f"host `{h}`" for h in p["network"] if h not in rv["network"]]
    out += [f"folder `{f}`" for f in p["fs"] if f not in rv["fs"]]
    out += [f"program `{g['id']}`" for g in p["programs"] if g["id"] not in rv["programs"]]
    if rv["signer"] and m.signer != rv["signer"]:
        out.append(f"signer {m.signer or 'none (unsigned)'}, reviewed {rv['signer']}")
    return out


def entry(record: Record, repo: dict, release: dict, asset: dict, m: Module) -> dict:
    man, over, p = m.manifest, record.modules.get(m.manifest["id"], {}), permissions(m)
    described = _text(man.get("description"), MAX_DESCRIPTION)
    description = over.get("description") or record.description or described
    summary = over.get("summary") or record.summary or _summary(described or _text(repo.get("description"), MAX_SUMMARY))
    licence = _text(man.get("license"), 256) or ((repo.get("license") or {}).get("spdx_id") or "")
    e = {
        "id": man["id"],
        "name": _text(man["name"], 100),
        "version": man["version"].strip(),
        "kind": m.kind,
        "tier": record.tier,
        "summary": summary,
        "description": description,
        "author": _text(man.get("author"), 100) or str((repo.get("owner") or {}).get("login", "")),
        "tags": over.get("tags", record.tags),
        "platforms": over.get("platforms") or record.platforms or platforms(m),
        "url": asset["browser_download_url"],
        "sha256": m.sha256,
        "size": m.size,
        "requires": [r for r in man.get("requires") or [] if isinstance(r, str) and ID_RE.match(r)] if m.kind == "plugin" else [],
        "permissions": p["groups"],
        "network": p["network"],
        "fs": p["fs"],
        "programs": [_text(g.get("name"), 100) or g["id"] for g in p["programs"]],
        "stamped": m.stamped,
        "signer": m.signer,
        "license": "" if licence == "NOASSERTION" else licence,
        "homepage": _https(man.get("homepage")) or _https(repo.get("homepage")) or repo.get("html_url"),
        "repository": _https(man.get("repository")) or repo.get("html_url"),
        "changelog": _https(man.get("changelog")) or release.get("html_url"),
        "released": str(release.get("published_at", "")),
        "tag": str(release.get("tag_name", "")),
        "record": record.slug,
    }
    if record.tier == "vetted":
        e["review"] = {"by": record.review["by"], "date": record.review["date"]}
    return e


@dataclass
class Result:
    entries: list[dict] = field(default_factory=list)
    errors: dict[str, list[str]] = field(default_factory=dict)  # by record slug
    held: dict[str, list[str]] = field(default_factory=dict)  # vetted, outside its review: by module id
    notes: dict[str, list[str]] = field(default_factory=dict)
    authors: dict[str, set[str]] = field(default_factory=dict)  # who may list each record on their own

    def fail(self, slug: str, why: str) -> None:
        self.errors.setdefault(slug, []).append(why)


def build(records: list[Record], gh: GitHub, previous: dict | None = None) -> Result:
    """One entry per module of each record's newest release. A record that can't be read today
    keeps the entries it had (`previous`), so a broken release or an outage never empties the
    index; a vetted module that asks for more than was reviewed stays at its reviewed version."""
    res, before = Result(), {}
    for e in (previous or {}).get("plugins", []):
        if isinstance(e, dict) and isinstance(e.get("id"), str):
            before[e["id"]] = e
    kept = lambda slug: [e for e in before.values() if e.get("record") == slug]
    owner: dict[str, str] = {}
    for record in records:
        try:
            repo = gh.repo(record.repo)
            if repo.get("private") or repo.get("visibility", "public") != "public":
                raise Refused(f"{record.repo} isn't a public repository")
            if repo.get("archived"):
                res.notes.setdefault(record.slug, []).append(f"{record.repo} is archived")
            release, assets = pick_release(record, gh.releases(record.repo))
        except Refused as e:
            res.fail(record.slug, str(e))
            res.entries += kept(record.slug)
            continue
        res.authors[record.slug] = {str((repo.get("owner") or {}).get("login", "")).lower(), str((release.get("author") or {}).get("login", "")).lower()} - {""}
        for asset in assets:
            name = str(asset.get("name"))
            try:
                m = inspect(gh.download(asset))
                mid = m.manifest["id"]
                if record.modules.get(mid, {}).get("hidden"):
                    continue
                if mid in owner:
                    raise Refused(f"the id {mid!r} is already listed by record `{owner[mid]}`")
                if mid in before and before[mid].get("record") not in (record.slug, None) and any(r.slug == before[mid].get("record") for r in records):
                    raise Refused(f"the id {mid!r} belongs to record `{before[mid].get('record')}`")
                owner[mid] = record.slug
                e = entry(record, repo, release, asset, m)
                if not e["license"]:
                    raise Refused(f"{record.repo} names no licence (in the manifest's `license` or a LICENSE file GitHub recognises)")
                beyond = outside_review(record, m)
                if beyond:
                    res.held[mid] = beyond
                    old = before.get(mid)
                    if old and old.get("record") == record.slug and old.get("tier") == "vetted":
                        res.entries.append(old)
                    continue
                res.entries.append(e)
            except Refused as e:
                res.fail(record.slug, f"{name}: {e}")
                res.entries += [o for o in kept(record.slug) if o.get("url", "").endswith("/" + name) and o["id"] not in owner]
    res.entries.sort(key=lambda e: (e["name"].lower(), e["id"]))
    return res


def index(entries: list[dict], previous: dict | None, source: str) -> dict:
    """The index document. `generated` only moves when an entry changes, so a run that finds
    nothing new writes the same bytes."""
    if previous and previous.get("plugins") == entries and previous.get("format") == FORMAT and previous.get("source") == source:
        generated = previous.get("generated")
    else:
        generated = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"format": FORMAT, "generated": generated, "source": source, "plugins": entries}


def catalogue(doc: dict) -> str:
    """The index as a page people read (the index branch's README)."""
    lines = [
        "# Plug-in index",
        "",
        "Generated from the records on `main` by `tools/market.py`; never edited by hand.",
        f"`index.json` is what the app reads. Last change: {doc['generated']}.",
        "",
        "| Plug-in | Version | Kind | Tier | Platforms | Permissions | Author |",
        "|---|---|---|---|---|---|---|",
    ]
    for e in doc["plugins"]:
        lines.append(
            f"| [{_cell(e['name'])}]({e['repository']}) | {_cell(e['version'])} | {'pixel filter' if e['kind'] == 'filter' else 'plug-in'} | {e['tier']} "
            f"| {', '.join(e['platforms'])} | {', '.join(e['permissions']) or 'none'} | {_cell(e['author'])} |"
        )
    return "\n".join(lines) + "\n"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("[", "\\[").replace("]", "\\]").replace("<", "&lt;")


def report(records: list[Record], record_errors: dict[str, str], res: Result) -> str:
    lines = ["## Marketplace check", ""]
    for slug, why in sorted(record_errors.items()):
        lines.append(f"- ❌ `plugins/{slug}.toml`: {_cell(why)}")
    for r in records:
        mine = [e for e in res.entries if e.get("record") == r.slug]
        for why in res.errors.get(r.slug, []):
            lines.append(f"- ❌ `{r.slug}`: {_cell(why)}")
        for why in res.notes.get(r.slug, []):
            lines.append(f"- ⚠️ `{r.slug}`: {_cell(why)}")
        for e in mine:
            asks = ", ".join(e["permissions"] + [f"host {h}" for h in e["network"]] + [f"program {p}" for p in e["programs"]] + [f"folder {f}" for f in e["fs"]])
            signed = f"signed by `{e['signer']}`" if e["signer"] else "unsigned"
            lines.append(
                f"- ✅ `{r.slug}` → **{_cell(e['name'])}** {_cell(e['version'])} (`{e['id']}`, {'pixel filter' if e['kind'] == 'filter' else 'plug-in'}, "
                f"{e['tier']}), {', '.join(e['platforms'])}; asks for: {asks or 'nothing'}; {signed}; {'stamped' if e['stamped'] else 'not stamped'}; {e['license']}"
            )
    for mid, beyond in sorted(res.held.items()):
        lines.append(f"- ⏸️ `{mid}` is held at its reviewed version: its newest release has {', '.join(beyond)}. A maintainer reviews it and updates `[review]`.")
    if len(lines) == 2:
        lines.append("Nothing to check.")
    return "\n".join(lines) + "\n"


# ───────────────────────────── commands ─────────────────────────────


def _previous(path: str | None) -> dict | None:
    if not path or not Path(path).is_file():
        return None
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else None
    except (ValueError, OSError):
        return None


def cmd_build(args) -> int:
    records, record_errors = read_records(Path(args.records))
    previous = _previous(args.previous)
    res = build(records, GitHub(Path(args.cache) if args.cache else None), previous)
    for slug, why in record_errors.items():
        res.fail(slug, why)
        res.entries += [e for e in (previous or {}).get("plugins", []) if isinstance(e, dict) and e.get("record") == slug]
    res.entries.sort(key=lambda e: (e["name"].lower(), e["id"]))
    doc = index(res.entries, previous, args.source)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "README.md").write_text(catalogue(doc), encoding="utf-8")
    text = report(records, record_errors, res)
    if args.report:
        Path(args.report).write_text(text, encoding="utf-8")
    problems = {"errors": res.errors, "held": res.held}
    if args.problems:
        Path(args.problems).write_text(json.dumps(problems, indent=1) + "\n", encoding="utf-8")
    print(text)
    print(f"{len(doc['plugins'])} entries written to {out / 'index.json'}")
    # an index was written either way: a record's trouble is reported, not fatal
    return 0


def cmd_check(args) -> int:
    records, record_errors = read_records(Path(args.records), args.only or None)
    for slug in args.only or []:
        if slug not in record_errors and not any(r.slug == slug for r in records):
            record_errors[slug] = "no such record"
    res = build(records, GitHub(Path(args.cache) if args.cache else None), _previous(args.previous))
    # a check lists nothing that is only there because it was listed before
    listed = {r.slug for r in records if not res.errors.get(r.slug)}
    for r in records:
        if r.slug in listed and not any(e.get("record") == r.slug for e in res.entries) and not res.held:
            res.fail(r.slug, "the release has no module to list")
    text = report(records, record_errors, res)
    ok = not record_errors and not res.errors
    if args.author:
        # who may list or change a record with no maintainer looking: someone the record names,
        # who also owns the repository or published the release
        author = args.author.lower()
        alone = ok and all(
            r.tier == "community" and author in {m.lower() for m in r.maintainers} and author in res.authors.get(r.slug, set()) for r in records
        )
        # a record that exists already answers to the maintainers it names now, not to those a
        # pull request writes into it
        for r in records if args.base else []:
            was = Path(args.base) / f"{r.slug}.toml"
            if was.is_file():
                try:
                    alone = alone and author in {m.lower() for m in read_record(was).maintainers}
                except Refused:
                    alone = False
        text += f"\nAutomatic listing for @{args.author}: {'yes' if alone else 'no, a maintainer reviews this'}.\n"
        if args.verdict:
            Path(args.verdict).write_text("auto\n" if alone else ("review\n" if ok else "fail\n"), encoding="utf-8")
    if args.report:
        Path(args.report).write_text(text, encoding="utf-8")
    print(text)
    return 0 if ok else 1


def cmd_inspect(args) -> int:
    try:
        m = inspect(Path(args.file).read_bytes())
    except (Refused, OSError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    manifest = {k: v for k, v in m.manifest.items() if k not in ("icon", "header")}
    print(json.dumps({"kind": m.kind, "sha256": m.sha256, "size": m.size, "stamped": m.stamped, "signer": m.signer,
                      "platforms": platforms(m), "permissions": permissions(m), "manifest": manifest}, indent=1, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="write index.json and its README from every record")
    b.add_argument("--out", required=True)
    b.add_argument("--report", help="write the run's report (Markdown) here")
    b.add_argument("--problems", help="write the records that failed and the modules held (JSON) here")
    b.set_defaults(run=cmd_build)
    c = sub.add_parser("check", help="check records as a pull request's CI does; exits 1 when one can't be listed")
    c.add_argument("--only", nargs="*", help="record slugs (default: all)")
    c.add_argument("--author", help="the pull request's author: says whether it may be listed with no maintainer looking")
    c.add_argument("--verdict", help="write auto, review or fail here (with --author)")
    c.add_argument("--base", help="the records as they are on main (with --author): an existing record's own maintainers decide")
    c.add_argument("--report")
    c.set_defaults(run=cmd_check)
    for p in (b, c):
        p.add_argument("--records", default=str(ROOT / "plugins"))
        p.add_argument("--previous", help="the index published now (entries are kept through an outage; ids keep their record)")
        p.add_argument("--cache", help="a folder that keeps downloaded modules between runs")
        p.add_argument("--source", default=os.environ.get("MARKETPLACE_SOURCE", "https://github.com/" + os.environ.get("GITHUB_REPOSITORY", "")))
    i = sub.add_parser("inspect", help="say what a .wasm file is, as the index would")
    i.add_argument("file")
    i.set_defaults(run=cmd_inspect)
    args = ap.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
