# Arabica AI Pro — starter source v0.2 (NOT a complete installer)

Desktop offline Russian ↔ Modern Standard Arabic (الفصحى) translator for Windows 10 x64.

## Scope
- **Only** Modern Standard Arabic and Russian, **no dialects**.
- CPU-only baseline; GPU optional after actual hardware verification. Target: 40 GB RAM, Windows 10, 30–100 GB free disk.
- Modes: accurate, literary, professional; history and user glossary in SQLite; TXT/DOCX import and export; bidirectional RTL UI.
- Translation performed **exclusively locally** by the bundled llama.cpp runtime with GGUF weights. No API key or network requirement at run time.
- Installation will be transferred from iPhone via USB storage; release must have installer chunks smaller than 2 GiB.

## What is available now
Python/PySide6 UI source, text splitting, translation prompt routing, preliminary offline llama-cli process adapter, glossary, basic quality warnings, and incomplete Windows build scripts. There is **NO GGUF model and NO llama-cli.exe in this source archive**, and **no validated Windows installer**. Real translation cannot run until these are integrated and tested.

## Developers
From a networked development machine:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m unittest discover -s tests -v
python run_arabica.py
```

To run translation you must add a legally redistributable model to `models/translator.gguf` and a compatible Windows `llama-cli.exe` and its runtime dependencies to `runtime/`. Check exact llama.cpp CLI flag support; the current backend is a **prototype**, reloads the model for each translation chunk and must be replaced with a persistent, version-pinned session for production. Never expose untrusted network endpoints.

## Release rules
Release is permitted only after full bundled model/runtime, verified licenses and SHA256, Windows 10 x64 smoke test on a disconnected machine, speed and accuracy measurements for **both** directions, and a functioning split installer. See [CLAUDE.md](CLAUDE.md) for Claude's work plan.
