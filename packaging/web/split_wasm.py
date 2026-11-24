#!/usr/bin/env python3
"""Split an oversized Trunk WASM asset without removing application features."""
import base64
import hashlib
import json
from pathlib import Path
import re
import sys


def integrity(data):
    return "sha384-" + base64.b64encode(hashlib.sha384(data).digest()).decode("ascii")


def split(wasm, limit):
    if limit < 1:
        raise ValueError("WASM file limit must be positive")
    data = wasm.read_bytes()
    if len(data) <= limit:
        return
    if not data.startswith(b"\0asm") or not wasm.name.endswith("_bg.wasm"):
        raise ValueError(f"{wasm.name}: expected an original wasm-bindgen module")
    site = wasm.parent
    index = site / "index.html"
    html = index.read_text(encoding="utf-8")
    glue = site / (wasm.name.removesuffix("_bg.wasm") + ".js")
    if not glue.is_file():
        raise ValueError(f"{wasm.name}: generated JavaScript binding not found")
    # Trunk's module import and SRI preload are both rewritten. Remove only
    # the preload for the original WASM file; it will no longer exist.
    links = re.findall(r"<link\b[^>]*>", html, flags=re.IGNORECASE)
    wasm_links = [tag for tag in links if f'./{wasm.name}' in tag]
    js_links = [tag for tag in links if f'./{glue.name}' in tag]
    if len(wasm_links) != 1 or len(js_links) != 1:
        raise ValueError("Unexpected Trunk preload layout; refusing to alter the generated page")
    module_import = re.compile(r"(from\s+['\"]\./)" + re.escape(glue.name) + r"(['\"])")
    if len(module_import.findall(html)) != 1:
        raise ValueError("Unexpected Trunk module import; refusing to alter the generated page")

    size = min(limit, 8 * 1024 * 1024)
    chunks = []
    for i, start in enumerate(range(0, len(data), size)):
        chunk = data[start:start + size]
        name = f"{wasm.stem}.part{i:03d}.wasm"
        chunks.append((name, chunk))

    binding_name = glue.stem + ".bindings.js"
    manifest = [{"name": name, "size": len(chunk), "integrity": integrity(chunk)} for name, chunk in chunks]
    # The wrapper preserves wasm-bindgen's default init and named exports.
    # Explicit callers passing other bytes/URLs still reach the original API.
    wrapper = (
        f"import bindgenInit from {json.dumps('./' + binding_name)};\n"
        f"export * from {json.dumps('./' + binding_name)};\n"
        f"const original = new URL({json.dumps('./' + wasm.name)}, import.meta.url);\n"
        f"const parts = {json.dumps(manifest)};\n"
        """
let download;
async function loadParts() {
    const buffers = await Promise.all(parts.map(async part => {
        const response = await fetch(new URL(part.name, import.meta.url), {integrity: part.integrity});
        if (!response.ok) throw new Error("Could not load FigCraft WASM: " + response.status);
        const bytes = new Uint8Array(await response.arrayBuffer());
        if (bytes.length !== part.size) throw new Error("Unexpected FigCraft WASM part size");
        return bytes;
    }));
    const bytes = new Uint8Array(parts.reduce((total, part) => total + part.size, 0));
    let offset = 0;
    for (const buffer of buffers) {
        bytes.set(buffer, offset);
        offset += buffer.length;
    }
    return bytes;
}
export default async function init(input) {
    const source = input && Object.getPrototypeOf(input) === Object.prototype
        ? input.module_or_path : input;
    const originalUrl = (typeof source === "string" || source instanceof URL)
        && new URL(source, globalThis.document?.baseURI ?? import.meta.url).href === original.href;
    if (source == null || originalUrl) {
        download ??= loadParts().catch(error => { download = undefined; throw error; });
        return bindgenInit({module_or_path: await download});
    }
    return bindgenInit(input);
}
"""
    ).encode("utf-8")
    if len(wrapper) > limit or glue.stat().st_size > limit:
        raise ValueError("JavaScript bindings exceed the configured file limit")
    wrapper_name = f"figcraft-web-{hashlib.sha256(wrapper).hexdigest()[:16]}.js"
    new_link = js_links[0].replace(glue.name, wrapper_name)
    new_link = re.sub(r'\bintegrity="[^"]*"', f'integrity="{integrity(wrapper)}"', new_link)
    html = html.replace(wasm_links[0], "").replace(js_links[0], new_link)
    html = module_import.sub(lambda m: m[1] + wrapper_name + m[2], html)
    if any((site / name).exists() for name in [binding_name, wrapper_name, *(name for name, _ in chunks)]):
        raise ValueError("Split output already exists; rebuild before packaging")

    for name, chunk in chunks:
        (site / name).write_bytes(chunk)
    (site / wrapper_name).write_bytes(wrapper)
    glue.rename(site / binding_name)
    index.write_text(html, encoding="utf-8")
    wasm.unlink()
    print(f"Split {wasm.name} into {len(chunks)} verified static assets ({len(data)} bytes)")


if __name__ == "__main__":
    split(Path(sys.argv[1]), int(sys.argv[2]))
