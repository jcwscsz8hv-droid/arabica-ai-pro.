# CLAUDE.md — lead development assignment

You are the lead engineer for **Arabica AI Pro**. Work directly in GitHub repository `jcwscsz8hv-droid/arabica-ai-pro.` (the repository name **ends with a period**). ChatGPT coordinates and GPT-6 Astra may independently review; **do not use Codex**.

## Product specification — non-negotiable
Windows 10 x64, 40 GB RAM, unknown CPU/GPU, 30–100 GB disk; installer downloaded onto iPhone and transferred on USB. Completely offline installation and inference without network, accounts or cloud APIs. Languages: Russian ↔ **literary Modern Standard Arabic only**; no colloquial dialects. No Google translator models/services. Deliver actual usable bundled-model program, not an HTML demonstration, API-dependent app or a script which downloads weights on the offline PC.

## Current repository state
If the starter archive has been imported, treat it as v0.2 research scaffold, **not production**. Inspect before changing. The `LlamaCliEngine` may not match the currently selected llama.cpp CLI flags/chat format and reloads weights on every chunk. Model/runtime binaries are absent. Do not claim successful real translation before testing actual model output.

## Execution order (code, commits, tests, evidence)
1. Bootstrap repo and document exact environment, model licensing, build limits and ARM/AVX CPU compatibility. Preserve this `CLAUDE.md` in repo.
2. Inspect and test current source; write regression tests before major refactors; fix Windows-specific Qt/RTL and file handling issues.
3. Research and **benchmark** openly licensed non-Google GGUF candidates for RU↔MSA; initial shortlist Qwen3.5-9B Q6_K and Qwen3-14B Q5_K_M. Do not assume bigger is better; verify current model architecture, chat template and llama.cpp support using pinned versions. Choose final model based on bilingual quality, peak RAM and speed; no fabricated performance numbers.
4. Replace temporary per-request `llama-cli` invocation with resilient *persistent* local inference backend, cancellation and token/stall timeouts; refuse all external endpoints and download paths. Test running both translation directions with real weights.
5. Implement contextual chunking, glossary term enforcement, separate translation and optional lexical analysis modes (word senses, diacritics and Cyrillic transliteration); keep neutral domain translation and exact/literary/professional modes.
6. Test terminology, negatives, proper names, dates, numerals, paragraph structure, long texts, Arabic shaping/RTL, Russian, mixed input, Cyrillic paths, missing/corrupted GGUF, kill/recovery, retry, exit and offline storage.
7. Build Windows x64 on an appropriate Windows CI/host. Package PySide6 + inference runtime and dependencies + licensed model data + fonts + dictionaries. Include manifest, actual SHA256, licenses, changelog, and checksums. Use a split Inno Setup installer: pieces <2 GiB for iPhone transfer. Confirm physical offline install test on Windows 10, without preinstalled Python/model/network.
8. Publish GitHub Release **only** if both real translation smoke tests and Windows offline installation pass. Otherwise say BLOCKED with exact missing assets/resources; do not publish pretend full release.

## Team work
Claude: main implementation, PRs, tests, CI and release. Astra: independent architecture and translation audits. ChatGPT: coordinates requirements and reviews outputs. Report each step using branch, commit hash, changed files, exact test commands, actual test results, and blockers. Work in small reviewable commits; do not modify unrelated repos.

## Acceptance gate
No "done" until `setup.exe` + its split data files and bundled GGUF install on an offline Windows 10 PC, both RU→MSA and MSA→RU translate a real sentence successfully, and SHA256/manifest/licenses/test evidence are published.
