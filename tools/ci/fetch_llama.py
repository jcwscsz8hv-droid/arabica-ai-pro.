"""Download a llama.cpp Windows CPU release build and record its provenance.

Usage: python tools/ci/fetch_llama.py --dest llama --info llama-info.json [--tag bNNNN]
Uses the GitHub REST API (GITHUB_TOKEN if present). Build-machine only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

API = "https://api.github.com/repos/ggml-org/llama.cpp/releases"
PATTERN = re.compile(r"bin-win-cpu-x64\.zip$")


def get(url: str, accept: str = "application/vnd.github+json") -> bytes:
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "arabica-ci"})
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok and "api.github.com" in url:
        req.add_header("Authorization", f"Bearer {tok}")
    with urllib.request.urlopen(req, timeout=600) as r:
        return r.read()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", required=True)
    ap.add_argument("--info", required=True)
    ap.add_argument("--tag", default="")
    ap.add_argument("--pattern", default=PATTERN.pattern)
    a = ap.parse_args()
    url = f"{API}/tags/{a.tag}" if a.tag else f"{API}/latest"
    rel = json.loads(get(url))
    names = [x["name"] for x in rel.get("assets", [])]
    print("release:", rel.get("tag_name"), "assets:", len(names))
    for n in names:
        print("  ", n)
    pat = re.compile(a.pattern)
    asset = next((x for x in rel["assets"] if pat.search(x["name"])), None)
    if not asset:
        print("ERROR: no asset matches", a.pattern)
        return 2
    data = get(asset["browser_download_url"], accept="application/octet-stream")
    digest = hashlib.sha256(data).hexdigest()
    dest = Path(a.dest)
    dest.mkdir(parents=True, exist_ok=True)
    zpath = dest.parent / asset["name"]
    zpath.write_bytes(data)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(dest)
    files = sorted(str(p.relative_to(dest)).replace("\\", "/") for p in dest.rglob("*") if p.is_file())
    info = {"tag": rel.get("tag_name"), "asset": asset["name"], "size": len(data), "sha256": digest,
            "published_at": rel.get("published_at"), "files": files, "all_assets": names}
    Path(a.info).write_text(json.dumps(info, indent=2), encoding="utf-8")
    print(json.dumps({k: info[k] for k in ("tag", "asset", "sha256")}, indent=2))
    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"tag={info['tag']}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
