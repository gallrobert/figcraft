#!/usr/bin/env python3
"""Exercise the generated split loader without building the full Rust app."""
import hashlib
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from split_wasm import split


class SplitWasmTests(unittest.TestCase):
    def fixture(self, directory):
        site = Path(directory)
        wasm = site / "figcraft-web-example_bg.wasm"
        payload = b"\0asm" + bytes(range(256)) * 80
        wasm.write_bytes(payload)
        glue = site / "figcraft-web-example.js"
        glue.write_text(
            "export const named = 42;\n"
            "export default async function init(input) { return input.module_or_path; }\n",
            encoding="utf-8",
        )
        (site / "index.html").write_text(
            '<link rel="modulepreload" href="./figcraft-web-example.js" integrity="sha384-old">'
            '<link rel="preload" href="./figcraft-web-example_bg.wasm" as="fetch">'
            '<script type="module">import init from \'./figcraft-web-example.js\';'
            "const wasm = await init({ module_or_path: './figcraft-web-example_bg.wasm' });</script>",
            encoding="utf-8",
        )
        (site / "package.json").write_text('{"type":"module"}', encoding="utf-8")
        return site, wasm, payload

    def test_split_and_load_exact_original_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            site, wasm, payload = self.fixture(temp)
            split(wasm, 8192)
            self.assertFalse(wasm.exists())
            parts = sorted(site.glob("*.part*.wasm"))
            self.assertEqual(len(parts), 3)
            self.assertEqual(b"".join(p.read_bytes() for p in parts), payload)
            self.assertTrue(all(p.stat().st_size <= 8192 for p in parts))
            html = (site / "index.html").read_text(encoding="utf-8")
            self.assertNotIn('href="./figcraft-web-example_bg.wasm"', html)
            match = re.search(r"from '\./([^']+)'", html)
            self.assertIsNotNone(match)
            loader = match.group(1)
            self.assertIn(f'href="./{loader}"', html)
            self.assertNotIn("sha384-old", html)
            node = """
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {pathToFileURL} from 'node:url';
const [file, expected] = process.argv.slice(1);
globalThis.fetch = async (url, options) => {
    const bytes = await readFile(url);
    assert.equal(options.integrity, 'sha384-' + createHash('sha384').update(bytes).digest('base64'));
    return new Response(bytes);
};
const {default: init, named} = await import(pathToFileURL(file).href);
assert.equal(named, 42);
for (const arg of [undefined, {module_or_path: new URL('./figcraft-web-example_bg.wasm', pathToFileURL(file))}]) {
    const bytes = await init(arg);
    assert.equal(createHash('sha256').update(bytes).digest('hex'), expected);
}
const explicit = new Uint8Array([1, 2, 3]);
assert.deepEqual(await init({module_or_path: explicit}), explicit);
"""
            subprocess.run(
                ["node", "--input-type=module", "-e", node, str(site / loader), hashlib.sha256(payload).hexdigest()],
                check=True,
            )

    def test_small_module_is_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            site, wasm, payload = self.fixture(temp)
            original = (site / "index.html").read_bytes()
            split(wasm, 32768)
            self.assertEqual(wasm.read_bytes(), payload)
            self.assertEqual((site / "index.html").read_bytes(), original)

    def test_unknown_html_is_refused_before_mutating_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            site, wasm, payload = self.fixture(temp)
            (site / "index.html").write_text("<html></html>", encoding="utf-8")
            with self.assertRaises(ValueError):
                split(wasm, 8192)
            self.assertEqual(wasm.read_bytes(), payload)
            self.assertFalse(list(site.glob("*.part*.wasm")))


if __name__ == "__main__":
    unittest.main()
