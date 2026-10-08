"""Discovery run for Arabica AI Pro (executed on a Windows CI runner).

Steps for one candidate model:
  1. Query Hugging Face for candidate GGUF repositories, record files/sizes/SHA-256/licences.
  2. Download the chosen GGUF file (verifying the LFS SHA-256).
  3. Start the bundled llama.cpp ``llama-server`` bound to 127.0.0.1 only.
  4. Run a small, author-written RU<->MSA smoke set with thinking disabled.
  5. Record cold start, tokens/s, peak RAM and raw outputs into JSON.

Nothing here is used by the end-user application; it only produces evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

CANDIDATES = {
    "qwen35-9b-q6k": {
        "quant": "q6_k",
        "repos": [
            "Qwen/Qwen3.5-9B-GGUF",
            "AtomicChat/Qwen3.5-9B-GGUF",
            "unsloth/Qwen3.5-9B-GGUF",
            "bartowski/Qwen_Qwen3.5-9B-GGUF",
            "lmstudio-community/Qwen3.5-9B-GGUF",
        ],
        "base_repo": "Qwen/Qwen3.5-9B",
    },
    "qwen3-14b-q5km": {
        "quant": "q5_k_m",
        "repos": ["Qwen/Qwen3-14B-GGUF"],
        "base_repo": "Qwen/Qwen3-14B",
    },
}

SYSTEM_RU_AR = (
    "You are a professional translator from Russian into Modern Standard Arabic (fusha). "
    "Translate the user's text faithfully. Do not add, omit or explain anything. "
    "Preserve numbers, dates, proper names, negation and modality exactly. "
    "Never use colloquial dialects. Output only the Arabic translation, nothing else."
)
SYSTEM_AR_RU = (
    "You are a professional translator from Modern Standard Arabic (fusha) into Russian. "
    "Translate the user's text faithfully into literary Russian. Do not add, omit or explain anything. "
    "Preserve numbers, dates, proper names, negation and modality exactly. "
    "Output only the Russian translation, nothing else."
)

# Author-written smoke sentences (not part of the closed test set).
SMOKE = [
    ("ru-ar", "Книга"),
    ("ru-ar", "Министр иностранных дел заявил, что переговоры не будут возобновлены до 15 марта 2027 года."),
    ("ru-ar", "Правительство выделило 2,5 миллиарда рублей на строительство 120 школ."),
    ("ru-ar", "Мы не можем согласиться с этим предложением, однако готовы продолжить диалог."),
    ("ru-ar", "Осенью в старом парке было тихо, и только ветер шуршал опавшими листьями."),
    ("ar-ru", "كتاب"),
    ("ar-ru", "أكد الأمين العام للأمم المتحدة أن الحل العسكري ليس خيارا مقبولا."),
    ("ar-ru", "وقعت الدولتان اتفاقية تعاون في مجال الطاقة المتجددة يوم 3 أبريل 2026."),
    ("ar-ru", "لم يحضر الوفد الاجتماع بسبب الظروف الجوية."),
    ("ar-ru", "تشير الدراسة إلى أن نسبة البطالة بين الشباب ارتفعت إلى 18 في المئة خلال العام الماضي."),
]


def sha256_file(path: Path, chunk: int = 16 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def hf_discover(cand: dict) -> dict:
    from huggingface_hub import HfApi

    api = HfApi()
    out: dict = {"repos": {}, "chosen": None}
    try:
        info = api.model_info(cand["base_repo"])
        out["base"] = {
            "repo": cand["base_repo"],
            "sha": info.sha,
            "license": (info.card_data or {}).get("license") if info.card_data else None,
            "tags": [t for t in (info.tags or []) if t.startswith(("license", "arxiv", "base_model"))],
            "pipeline_tag": info.pipeline_tag,
        }
    except Exception as e:  # noqa: BLE001
        out["base"] = {"repo": cand["base_repo"], "error": repr(e)[:300]}
    for repo in cand["repos"]:
        try:
            info = api.model_info(repo, files_metadata=True)
        except Exception as e:  # noqa: BLE001
            out["repos"][repo] = {"error": repr(e)[:300]}
            continue
        files = []
        for s in info.siblings or []:
            if not s.rfilename.lower().endswith(".gguf"):
                continue
            files.append({
                "name": s.rfilename,
                "size": s.size,
                "sha256": getattr(s.lfs, "sha256", None) if s.lfs else None,
            })
        lic = None
        if info.card_data:
            lic = info.card_data.get("license")
        out["repos"][repo] = {"sha": info.sha, "license": lic, "gguf_files": files,
                              "tags": [t for t in (info.tags or []) if t.startswith(("license", "base_model"))]}
        if out["chosen"] is None:
            q = cand["quant"]
            for f in files:
                n = f["name"].lower()
                if q in n and "mmproj" not in n and not re.search(r"-\d{5}-of-\d{5}", n):
                    out["chosen"] = {"repo": repo, "revision": info.sha, **f}
                    break
    return out


def download(chosen: dict, dest_dir: Path) -> tuple[Path, dict]:
    from huggingface_hub import hf_hub_download

    t0 = time.time()
    p = Path(hf_hub_download(chosen["repo"], chosen["name"], revision=chosen["revision"],
                             local_dir=str(dest_dir)))
    dt = time.time() - t0
    t1 = time.time()
    digest = sha256_file(p)
    return p, {"download_s": round(dt, 1), "hash_s": round(time.time() - t1, 1),
               "sha256": digest, "sha256_matches_lfs": digest == chosen.get("sha256"),
               "size": p.stat().st_size}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class PeakMem(threading.Thread):
    def __init__(self, pid: int):
        super().__init__(daemon=True)
        self.pid = pid
        self.peak_rss = 0
        self.peak_wset = 0
        self.peak_private = 0
        self.stop = False

    def run(self):
        import psutil

        try:
            p = psutil.Process(self.pid)
        except Exception:  # noqa: BLE001
            return
        while not self.stop:
            try:
                mi = p.memory_info()
                self.peak_rss = max(self.peak_rss, mi.rss)
                self.peak_wset = max(self.peak_wset, getattr(mi, "peak_wset", 0))
                self.peak_private = max(self.peak_private, getattr(mi, "private", 0))
            except Exception:  # noqa: BLE001
                return
            time.sleep(0.5)


def post(port: int, key: str, path: str, body: dict, timeout: float = 900) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def run_smoke(server: Path, model: Path, ctx: int, threads: int, logdir: Path) -> dict:
    port = free_port()
    key = secrets.token_hex(16)
    help_txt = subprocess.run([str(server), "--help"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace").stdout
    args = [str(server), "-m", str(model), "--host", "127.0.0.1", "--port", str(port),
            "-c", str(ctx), "-t", str(threads), "-np", "1", "--jinja", "--api-key", key]
    if "--no-webui" in help_txt:
        args.append("--no-webui")
    log = (logdir / f"server-{model.stem}.log").open("w", encoding="utf-8", errors="replace")
    t0 = time.time()
    proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT)
    mem = PeakMem(proc.pid)
    mem.start()
    ready = None
    while time.time() - t0 < 900:
        if proc.poll() is not None:
            break
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as r:
                if r.status == 200:
                    ready = time.time() - t0
                    break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1)
    res: dict = {"args": args[1:], "cold_start_s": round(ready, 1) if ready else None,
                 "exit_code_early": proc.poll(), "cases": []}
    if ready is None:
        proc.kill()
        mem.stop = True
        log.close()
        return res
    for direction, text in SMOKE:
        system = SYSTEM_RU_AR if direction == "ru-ar" else SYSTEM_AR_RU
        body = {
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": text}],
            "temperature": 0, "top_k": 1, "max_tokens": 512, "stream": False,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        t1 = time.time()
        try:
            r = post(port, key, "/v1/chat/completions", body)
            msg = r["choices"][0]["message"]
            res["cases"].append({
                "direction": direction, "source": text, "output": msg.get("content"),
                "reasoning_content": msg.get("reasoning_content"),
                "has_think_tag": "<think>" in (msg.get("content") or ""),
                "wall_s": round(time.time() - t1, 2), "timings": r.get("timings"),
                "usage": r.get("usage"),
            })
        except Exception as e:  # noqa: BLE001
            res["cases"].append({"direction": direction, "source": text, "error": repr(e)[:500]})
    # No-auth request must be rejected (local hardening check).
    try:
        urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/models"), timeout=10)
        res["unauthenticated_request_rejected"] = False
    except Exception:  # noqa: BLE001
        res["unauthenticated_request_rejected"] = True
    proc.terminate()
    try:
        proc.wait(30)
    except subprocess.TimeoutExpired:
        proc.kill()
    mem.stop = True
    time.sleep(1)
    log.close()
    res["peak_rss_bytes"] = mem.peak_rss
    res["peak_working_set_bytes"] = mem.peak_wset
    res["peak_private_bytes"] = mem.peak_private
    tps = [c["timings"]["predicted_per_second"] for c in res["cases"]
           if c.get("timings") and c["timings"].get("predicted_per_second")]
    pps = [c["timings"]["prompt_per_second"] for c in res["cases"]
           if c.get("timings") and c["timings"].get("prompt_per_second")]
    res["gen_tokens_per_s_median"] = sorted(tps)[len(tps) // 2] if tps else None
    res["prompt_tokens_per_s_median"] = sorted(pps)[len(pps) // 2] if pps else None
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate", required=True, choices=sorted(CANDIDATES))
    ap.add_argument("--llama-dir", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ctx", type=int, default=4096)
    a = ap.parse_args()

    import psutil

    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    report: dict = {
        "candidate": a.candidate,
        "host": {
            "platform": platform.platform(), "machine": platform.machine(),
            "cpu_logical": psutil.cpu_count(), "cpu_physical": psutil.cpu_count(logical=False),
            "ram_total_bytes": psutil.virtual_memory().total,
            "disk_free_work_bytes": shutil.disk_usage(work).free,
        },
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    cand = CANDIDATES[a.candidate]
    report["hf"] = hf_discover(cand)
    chosen = report["hf"].get("chosen")
    if not chosen:
        report["status"] = "BLOCKED: no matching GGUF found"
        (out / f"{a.candidate}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
        return 0
    try:
        model, dl = download(chosen, work / "models")
        report["download"] = dl
        report["disk_free_after_download_bytes"] = shutil.disk_usage(work).free
    except Exception as e:  # noqa: BLE001
        report["status"] = f"BLOCKED: download failed {e!r}"[:500]
        (out / f"{a.candidate}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
        return 0
    exe = "llama-server.exe" if os.name == "nt" else "llama-server"
    found = sorted(Path(a.llama_dir).rglob(exe))
    if not found:
        report["status"] = f"BLOCKED: {exe} not found in {a.llama_dir}"
        (out / f"{a.candidate}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
        return 0
    server = found[0]
    report["server_path"] = str(server)
    threads = psutil.cpu_count(logical=False) or 4
    report["smoke"] = run_smoke(server, model, a.ctx, threads, out)
    ok = [c for c in report["smoke"]["cases"] if c.get("output")]
    report["status"] = "OK" if len(ok) == len(SMOKE) else f"PARTIAL {len(ok)}/{len(SMOKE)}"
    (out / f"{a.candidate}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps({k: report.get(k) for k in ("candidate", "status")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
