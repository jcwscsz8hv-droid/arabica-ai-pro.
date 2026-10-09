"""Run the closed evaluation set (docs/EVAL_CASES.jsonl) through the REAL application pipeline
(arabica.service.Translator + LlamaServerEngine) with a pinned GGUF model. Build/CI machine only.

  python tools/ci/bench.py --model qwen35-9b-q6k --llama-dir llama --work D:\\work --out out \
         --shard 0 --shards 4

Writes out/results-<model>-<shard>.jsonl (one line per case) and out/bench-<model>-<shard>.json
(timing, memory). Scoring/aggregation: tools/ci/bench_report.py.
Mode per category: literary -> literary; official/diplomatic/military/science -> professional;
others -> accurate (documented in docs/EVAL_RUBRIC.md).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from arabica.backend import BackendError, EngineConfig, LlamaServerEngine  # noqa: E402
from arabica.prompts import PROMPT_VERSION  # noqa: E402
from arabica.service import Translator  # noqa: E402

MODE_BY_CATEGORY = {"literary": "literary", "official_political": "professional", "diplomatic": "professional",
                    "military": "professional", "science_tech": "professional"}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(16 << 20), b""):
            h.update(b)
    return h.hexdigest()


def fetch_model(pin: dict, work: Path) -> Path:
    from huggingface_hub import hf_hub_download

    p = Path(hf_hub_download(pin["repo"], pin["file"], revision=pin["revision"], local_dir=str(work / "models")))
    digest = sha256(p)
    if digest != pin["sha256"]:
        raise SystemExit(f"SHA-256 mismatch for {pin['file']}: {digest}")
    return p


class Mem(threading.Thread):
    def __init__(self, pid):
        super().__init__(daemon=True)
        self.pid, self.peak, self.stop = pid, 0, False

    def run(self):
        import psutil
        try:
            p = psutil.Process(self.pid)
            while not self.stop:
                self.peak = max(self.peak, p.memory_info().rss)
                time.sleep(0.5)
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--llama-dir", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--ctx", type=int, default=8192)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    pins = json.loads((ROOT / "tools/ci/pins.json").read_text(encoding="utf-8"))
    pin = pins["models"][a.model]
    work, out = Path(a.work), Path(a.out)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    cases = [json.loads(l) for l in (ROOT / "docs/EVAL_CASES.jsonl").read_text(encoding="utf-8").splitlines() if l]
    cases = [c for i, c in enumerate(cases) if i % a.shards == a.shard]
    if a.limit:
        cases = cases[:a.limit]
    for c in cases:
        c["mode"] = MODE_BY_CATEGORY.get(c["category"], "accurate")
    cases.sort(key=lambda c: (c["direction"], c["mode"]))  # maximise prompt-prefix cache reuse

    t0 = time.time()
    model = fetch_model(pin, work)
    dl_s = time.time() - t0
    exe = "llama-server.exe" if os.name == "nt" else "llama-server"
    server = sorted(Path(a.llama_dir).rglob(exe))[0]
    eng = LlamaServerEngine(EngineConfig(server, model, context_tokens=a.ctx, stall_timeout_s=600,
                                         request_timeout_s=3600), log_path=out / f"server-{a.model}-{a.shard}.log")
    t1 = time.time()
    eng.start()
    cold = time.time() - t1
    mem = Mem(eng.proc.pid)
    mem.start()
    tr = Translator(eng, store=None, model_name=a.model)
    res_path = out / f"results-{a.model}-{a.shard}.jsonl"
    n_ok = 0
    with res_path.open("w", encoding="utf-8") as f:
        for c in cases:
            s = time.time()
            rec = {"id": c["id"], "model": a.model, "mode": c["mode"], "prompt_version": PROMPT_VERSION}
            try:
                r = tr.translate(c["source"], c["direction"], c["mode"])
                rec.update(output=r.text, issues=[{"code": i.code, "kind": i.kind, "severity": i.severity}
                                                  for i in r.warnings])
                n_ok += 1
            except BackendError as ex:
                rec.update(output=None, error=str(ex))
            rec["seconds"] = round(time.time() - s, 2)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            print(f"{c['id']} {rec['seconds']}s :: {(rec.get('output') or rec.get('error'))[:100]}", flush=True)
    mem.stop = True
    eng.stop()
    meta = {"model": a.model, "pin": pin, "shard": a.shard, "shards": a.shards, "cases": len(cases), "ok": n_ok,
            "download_s": round(dl_s, 1), "cold_start_s": round(cold, 1), "peak_rss_bytes": mem.peak,
            "total_s": round(time.time() - t0, 1), "ctx": a.ctx, "prompt_version": PROMPT_VERSION}
    try:
        import psutil, platform
        meta["host"] = {"platform": platform.platform(), "cpu_physical": psutil.cpu_count(logical=False),
                        "cpu_logical": psutil.cpu_count(), "ram": psutil.virtual_memory().total}
    except Exception:  # noqa: BLE001
        pass
    (out / f"bench-{a.model}-{a.shard}.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    shutil.rmtree(work / "models", ignore_errors=True)
    print(json.dumps({k: meta[k] for k in ("model", "shard", "cases", "ok", "total_s")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
