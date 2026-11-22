"""The tool's own tests: hand-made modules, a GitHub that serves from a table.

    python -m unittest discover -s tests
"""

import base64
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import market  # noqa: E402
from market import Refused  # noqa: E402

import wasmtime  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402


def _wat_text(text: str) -> str:
    return "".join(chr(b) if 32 <= b < 127 and chr(b) not in '"\\' else f"\\{b:02x}" for b in text.encode())


def plugin(manifest: dict, stamp: bool = False, imports: str = "", stamped_as: dict | None = None) -> bytes:
    text = json.dumps(manifest)
    section = f'(@custom "{market.MANIFEST_SECTION}" "{_wat_text(json.dumps(stamped_as) if stamped_as else text)}")' if stamp or stamped_as else ""
    return bytes(wasmtime.wat2wasm(f"""(module {imports} (memory (export "memory") 2)
      (data (i32.const 16) "{_wat_text(text)}")
      (func (export "lcc_abi_version") (result i32) (i32.const 1))
      (func (export "lcc_manifest") (result i64) (i64.or (i64.shl (i64.const {len(text.encode())}) (i64.const 32)) (i64.const 16)))
      (func (export "lcc_alloc") (param i32) (result i32) (i32.const 65536))
      (func (export "lcc_handle") (param i32 i32) (result i64) (i64.const 0))
      {section})"""))


def filter_module(manifest: dict, body: str = "") -> bytes:
    text = json.dumps(manifest)
    manifest_fn = body or f"(i64.or (i64.shl (i64.const {len(text.encode())}) (i64.const 32)) (i64.const 16))"
    return bytes(wasmtime.wat2wasm(f"""(module (memory (export "memory") 2)
      (data (i32.const 16) "{_wat_text(text)}")
      (func (export "pc_abi_version") (result i32) (i32.const 1))
      (func (export "pc_manifest") (result i64) {manifest_fn})
      (func (export "pc_alloc") (param i32) (result i32) (i32.const 65536))
      (func (export "pc_filter") (param i32 i32 i32 i32 i32 i32) (result i32) (i32.const 0)))"""))


def _leb(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def sign(wasm: bytes, key: Ed25519PrivateKey, over: bytes | None = None) -> bytes:
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    doc = {"v": 1, "key": base64.b64encode(public).decode(), "sig": base64.b64encode(key.sign(wasm if over is None else over)).decode()}
    name = market.SIGNATURE_SECTION.encode()
    payload = _leb(len(name)) + name + json.dumps(doc).encode()
    return wasm + b"\0" + _leb(len(payload)) + payload


PLUGIN = {"id": "org.example.tools", "name": "Tools", "version": "1.2.0", "description": "Does a great many useful things. And more things.", "permissions": {"catalog": True}}
FILTER = {"id": "org.example.look", "name": "Look", "version": "0.3", "kind": "filter", "description": "A look"}


class FakeGitHub:
    def __init__(self):
        self.repos, self.rels, self.files = {}, {}, {}

    def add(self, repo: str, tag: str, files: dict[str, bytes], prerelease=False, published="2026-01-01T00:00:00Z", author="alice", license="MIT"):
        self.repos[repo] = {"html_url": f"https://github.com/{repo}", "owner": {"login": repo.split("/")[0]}, "private": False, "visibility": "public",
                            "license": {"spdx_id": license} if license else None, "description": "A repository"}
        assets = []
        for name, data in files.items():
            url = f"https://github.com/{repo}/releases/download/{tag}/{name}"
            self.files[url] = data
            assets.append({"id": len(self.files), "name": name, "size": len(data), "updated_at": published, "browser_download_url": url})
        self.rels.setdefault(repo, []).append({"tag_name": tag, "draft": False, "prerelease": prerelease, "published_at": published, "assets": assets,
                                               "author": {"login": author}, "html_url": f"https://github.com/{repo}/releases/tag/{tag}"})

    def repo(self, repo):
        if repo not in self.repos:
            raise Refused(f"GitHub answered 404 for repos/{repo}")
        return self.repos[repo]

    def releases(self, repo):
        return self.rels.get(repo, [])

    def download(self, asset):
        return self.files[asset["browser_download_url"]]


def record(slug="tools", **kw) -> market.Record:
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / f"{slug}.toml"
        lines = []
        tables = {k: v for k, v in kw.items() if isinstance(v, dict)}
        for k, v in {"repo": "alice/tools", "maintainers": ["alice"], **kw}.items():
            if k not in tables:
                lines.append(f"{k} = {json.dumps(v)}")
        for k, v in tables.items():
            if all(isinstance(b, dict) for b in v.values()):
                for name, inner in v.items():
                    lines.append(f'[{k}."{name}"]')
                    lines += [f"{a} = {json.dumps(b)}" for a, b in inner.items()]
                continue
            lines.append(f"[{k}]")
            lines += [f"{a} = {b if isinstance(b, dt.date) else json.dumps(b)}" for a, b in v.items()]
        path.write_text("\n".join(lines))
        return market.read_record(path)


class Inspect(unittest.TestCase):
    def test_a_plugin_and_a_filter_are_read_in_the_sandbox(self):
        m = market.inspect(plugin(PLUGIN))
        self.assertEqual((m.kind, m.manifest["id"], m.stamped, m.signer), ("plugin", "org.example.tools", False, None))
        self.assertEqual(market.permissions(m)["groups"], ["catalog"])
        f = market.inspect(filter_module(FILTER))
        self.assertEqual((f.kind, market.permissions(f)["groups"], market.platforms(f)), ("filter", [], ["macos", "windows", "linux", "android"]))

    def test_a_stamp_must_be_what_the_module_answers(self):
        self.assertTrue(market.inspect(plugin(PLUGIN, stamp=True)).stamped)
        with self.assertRaisesRegex(Refused, "stamped manifest differs"):
            market.inspect(plugin(PLUGIN, stamped_as={**PLUGIN, "permissions": {}}))

    def test_a_signature_is_valid_or_the_file_is_refused(self):
        key = Ed25519PrivateKey.generate()
        wasm = plugin(PLUGIN, stamp=True)
        signer = market.inspect(sign(wasm, key)).signer
        self.assertRegex(signer, r"^[0-9a-f]{4}( [0-9a-f]{4}){3}$")
        with self.assertRaisesRegex(Refused, "doesn't match"):
            market.inspect(sign(wasm, key, over=b"something else"))
        with self.assertRaisesRegex(Refused, "more than one signature"):
            market.inspect(sign(sign(wasm, key), key))

    def test_hostile_modules_are_refused_not_run_away_with(self):
        cases = {
            "not wasm": b"MZ\x90\0" * 8,
            "truncated": plugin(PLUGIN)[:40],
            "empty": b"",
            "wasi import": plugin(PLUGIN, imports='(import "wasi_snapshot_preview1" "fd_write" (func (param i32 i32 i32 i32) (result i32)))'),
            "neither kind": bytes(wasmtime.wat2wasm('(module (memory (export "memory") 1))')),
            "spins forever": filter_module(FILTER, body="(loop (br 0)) (i64.const 0)"),
            "manifest outside memory": filter_module(FILTER, body="(i64.or (i64.shl (i64.const 64) (i64.const 32)) (i64.const 0x7ffffff0))"),
            "manifest not json": plugin(PLUGIN).replace(b'{"id"', b'<"id"'),
            "bad id": plugin({**PLUGIN, "id": "has spaces"}),
            "version not semver": plugin({**PLUGIN, "version": "1.2"}),
            "no name": plugin({**PLUGIN, "name": " "}),
            "filter with an import": bytes(wasmtime.wat2wasm('(module (import "host" "log" (func (param i32 i32))) (memory (export "memory") 1)'
                                                             ' (func (export "pc_abi_version") (result i32) (i32.const 1)) (func (export "pc_manifest") (result i64) (i64.const 0))'
                                                             ' (func (export "pc_alloc") (param i32) (result i32) (i32.const 0)) (func (export "pc_filter")))')),
        }
        for name, wasm in cases.items():
            with self.subTest(name), self.assertRaises(Refused):
                market.inspect(wasm)

    def test_a_manifest_export_that_calls_the_host_is_refused(self):
        wasm = bytes(wasmtime.wat2wasm('(module (import "host" "log" (func $log (param i32 i32))) (memory (export "memory") 1)'
                                       ' (func (export "lcc_abi_version") (result i32) (i32.const 1))'
                                       ' (func (export "lcc_manifest") (result i64) (call $log (i32.const 0) (i32.const 0)) (i64.const 0))'
                                       ' (func (export "lcc_alloc") (param i32) (result i32) (i32.const 0)) (func (export "lcc_handle") (param i32 i32) (result i64) (i64.const 0)))'))
        with self.assertRaisesRegex(Refused, "can't answer"):
            market.inspect(wasm)

    def test_programs_decide_the_platforms(self):
        programs = [{"id": "tool", "name": "Tool", "macBundleIds": ["com.example.tool"], "windowsExe": "tool.exe"}]
        m = market.inspect(plugin({**PLUGIN, "permissions": {"programs": programs}}))
        self.assertEqual(market.platforms(m), ["macos", "windows"])


class Records(unittest.TestCase):
    def test_a_record_is_checked_field_by_field(self):
        self.assertEqual(record().tier, "community")
        bad = [{"repo": "not a repo"}, {"maintainers": []}, {"tier": "trusted"}, {"assets": "*.zip"}, {"platforms": ["amiga"]}, {"tags": ["Has Space"]},
               {"colour": "red"}, {"tier": "vetted"}, {"tier": "vetted", "review": {"by": "bob"}}, {"modules": {"x": {"colour": 1}}}]
        for kw in bad:
            with self.subTest(kw), self.assertRaises(Refused):
                record(**kw)
        self.assertEqual(record(tier="vetted", review={"by": "bob", "date": dt.date(2026, 1, 2)}).review["date"], "2026-01-02")

    def test_the_slug_is_the_file_name(self):
        with self.assertRaises(Refused):
            record(slug="Has_Caps")


class Build(unittest.TestCase):
    def test_the_newest_release_with_a_module_is_listed(self):
        gh = FakeGitHub()
        gh.add("alice/tools", "v1.0.0", {"tools.wasm": plugin({**PLUGIN, "version": "1.0.0"})})
        gh.add("alice/tools", "v1.2.0", {"tools.wasm": plugin(PLUGIN)}, published="2026-02-01T00:00:00Z")
        gh.add("alice/tools", "samples", {"scan.tif": b"II*\0"}, published="2026-03-01T00:00:00Z")
        gh.add("alice/tools", "v2.0.0-beta.1", {"tools.wasm": plugin({**PLUGIN, "version": "2.0.0-beta.1"})}, prerelease=True)
        res = market.build([record()], gh)
        self.assertEqual(res.errors, {})
        (e,) = res.entries
        self.assertEqual((e["version"], e["tier"], e["record"], e["summary"], e["license"]), ("1.2.0", "community", "tools", "Does a great many useful things", "MIT"))
        self.assertEqual(e["url"], "https://github.com/alice/tools/releases/download/v1.2.0/tools.wasm")
        (beta,) = market.build([record(prerelease=True)], gh).entries
        self.assertEqual(beta["version"], "2.0.0-beta.1")

    def test_one_release_can_hold_several_modules_and_hide_one(self):
        gh = FakeGitHub()
        gh.add("alice/tools", "v1", {"a.wasm": plugin(PLUGIN), "b.wasm": filter_module(FILTER), "c.wasm": filter_module({**FILTER, "id": "org.example.hide"})})
        res = market.build([record(modules={"org.example.hide": {"hidden": True}})], gh)
        self.assertEqual(sorted(e["id"] for e in res.entries), ["org.example.look", "org.example.tools"])

    def test_a_broken_release_keeps_what_was_listed(self):
        gh = FakeGitHub()
        gh.add("alice/tools", "v1.2.0", {"tools.wasm": plugin(PLUGIN)})
        before = market.index(market.build([record()], gh).entries, None, "src")
        gh.add("alice/tools", "v1.3.0", {"tools.wasm": b"not wasm at all"}, published="2026-05-01T00:00:00Z")
        res = market.build([record()], gh, before)
        self.assertIn("tools", res.errors)
        self.assertEqual([e["version"] for e in res.entries], ["1.2.0"])
        gone = market.build([record(repo="alice/gone")], gh, before)
        self.assertEqual([e["version"] for e in gone.entries], ["1.2.0"])

    def test_an_id_stays_with_its_record(self):
        gh = FakeGitHub()
        gh.add("alice/tools", "v1.2.0", {"tools.wasm": plugin(PLUGIN)})
        gh.add("mallory/tools", "v9.0.0", {"tools.wasm": plugin({**PLUGIN, "version": "9.0.0"})}, author="mallory")
        res = market.build([record(), record(slug="zz-copy", repo="mallory/tools", maintainers=["mallory"])], gh)
        self.assertEqual([(e["record"], e["version"]) for e in res.entries], [("tools", "1.2.0")])
        self.assertIn("already listed", res.errors["zz-copy"][0])
        # and when the copy sorts first, the published index says whose it is
        before = market.index(market.build([record()], gh).entries, None, "src")
        res = market.build([record(slug="aa-copy", repo="mallory/tools", maintainers=["mallory"]), record()], gh, before)
        self.assertEqual([(e["record"], e["version"]) for e in res.entries], [("tools", "1.2.0")])

    def test_a_vetted_module_is_held_when_it_asks_for_more_than_was_reviewed(self):
        gh = FakeGitHub()
        review = {"by": "bob", "date": dt.date(2026, 1, 2), "permissions": ["catalog"]}
        gh.add("alice/tools", "v1.2.0", {"tools.wasm": plugin(PLUGIN)})
        first = market.build([record(tier="vetted", review=review)], gh)
        self.assertEqual((first.held, first.entries[0]["tier"], first.entries[0]["review"]["by"]), ({}, "vetted", "bob"))
        before = market.index(first.entries, None, "src")
        wider = {**PLUGIN, "version": "1.3.0", "permissions": {"catalog": True, "network": ["evil.example"]}}
        gh.add("alice/tools", "v1.3.0", {"tools.wasm": plugin(wider)}, published="2026-05-01T00:00:00Z")
        res = market.build([record(tier="vetted", review=review)], gh, before)
        self.assertEqual([e["version"] for e in res.entries], ["1.2.0"])
        self.assertEqual(res.held["org.example.tools"], ["permission `network`", "host `evil.example`"])
        review.update(permissions=["catalog", "network"], network=["evil.example"])
        self.assertEqual([e["version"] for e in market.build([record(tier="vetted", review=review)], gh, before).entries], ["1.3.0"])

    def test_a_repository_without_a_licence_is_not_listed(self):
        gh = FakeGitHub()
        gh.add("alice/tools", "v1.2.0", {"tools.wasm": plugin(PLUGIN)}, license=None)
        res = market.build([record()], gh)
        self.assertEqual(res.entries, [])
        self.assertIn("licence", res.errors["tools"][0])

    def test_the_index_only_changes_when_an_entry_does(self):
        gh = FakeGitHub()
        gh.add("alice/tools", "v1.2.0", {"tools.wasm": plugin(PLUGIN)})
        entries = market.build([record()], gh).entries
        first = market.index(entries, None, "src")
        first["generated"] = "2020-01-01T00:00:00Z"
        self.assertEqual(market.index(entries, first, "src"), first)
        self.assertIn("| [Tools]", market.catalogue(first))

    def test_who_may_be_listed_without_a_maintainer(self):
        gh = FakeGitHub()
        gh.add("org/tools", "v1.2.0", {"tools.wasm": plugin(PLUGIN)}, author="carol")
        res = market.build([record(repo="org/tools", maintainers=["carol"])], gh)
        self.assertEqual(res.authors["tools"], {"org", "carol"})


if __name__ == "__main__":
    unittest.main()
