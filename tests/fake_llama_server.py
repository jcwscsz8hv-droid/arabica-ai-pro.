"""Minimal stand-in for llama.cpp `llama-server` used by integration tests (no model, loopback only).

Behaviour is deterministic so the full client path (process start, health, auth, SSE streaming,
cancellation, JSON-schema mode) can be tested without weights. NOT a translator.
Special inputs: "SLOW" streams slowly (cancel tests); "CRASH" exits the process.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DIGITS = re.compile(r"\d+(?:[.,]\d+)?")


def fake_translate(system: str, text: str) -> str:
    nums = " ".join(DIGITS.findall(text))
    if system.startswith("You add full vocalisation"):
        return "".join(ch + ("َ" if "ء" <= ch <= "ي" else "") for ch in text)
    if "into Modern Standard Arabic" in system:
        out = "هذه ترجمة تجريبية"
        if re.search(r"(?<![А-Яа-яЁё])(не|нет)(?![А-Яа-яЁё])", text):
            out = "هذه ليست ترجمة"
        return (out + " " + nums).strip() + "." if "SHORT INPUT" not in system else "كتاب"
    out = "Это тестовый перевод"
    if re.search(r"(^|\s)(لا|لم|لن|ليس)(\s|$)", text):
        out = "Это не перевод"
    keep = " ".join(re.findall(r"⟦\d+⟧", text))
    return (out + " " + nums + " " + keep).strip() + "."


class H(BaseHTTPRequestHandler):
    key = ""

    def log_message(self, *a):  # quiet
        pass

    def _auth(self) -> bool:
        if self.headers.get("Authorization") != f"Bearer {self.key}":
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"error":"unauthorized"}')
            return False
        return True

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return
        if not self._auth():
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        if not self._auth():
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        msgs = body["messages"]
        system, user = msgs[0]["content"], msgs[-1]["content"]
        if user == "CRASH":
            sys.exit(3)
        assert body.get("chat_template_kwargs", {}).get("enable_thinking") is False
        if body.get("response_format"):
            text = json.dumps({"lemma": user, "part_of_speech": "сущ.", "pos_confident": True,
                               "vocalized": "كِتَاب" if user == "كتاب" else "", "morphology": "",
                               "senses": [{"translation": "книга", "context_label": "общ.",
                                           "example_source": "x", "example_translation": "y"}],
                               "ambiguity_note": ""}, ensure_ascii=False)
        else:
            text = fake_translate(system, user)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        delay = 0.3 if user.startswith("SLOW") else 0.0
        for tok in re.findall(r"\S+\s*", text):
            chunk = {"choices": [{"delta": {"content": tok}, "finish_reason": None}]}
            try:
                self.wfile.write(b"data: " + json.dumps(chunk, ensure_ascii=False).encode() + b"\n\n")
                self.wfile.flush()
            except OSError:
                return
            time.sleep(delay)
        end = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
        self.wfile.write(b"data: " + json.dumps(end).encode() + b"\n\ndata: [DONE]\n\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-m")
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    ap.add_argument("--api-key")
    ap.add_argument("-c")
    ap.add_argument("-t")
    ap.add_argument("-np")
    ap.add_argument("--jinja", action="store_true")
    a, _ = ap.parse_known_args()
    assert a.host == "127.0.0.1", "must bind loopback only"
    if open(a.m, "rb").read(4) != b"GGUF":
        print("failed to load model", flush=True)
        sys.exit(1)
    H.key = a.api_key
    ThreadingHTTPServer((a.host, a.port), H).serve_forever()


if __name__ == "__main__":
    main()
