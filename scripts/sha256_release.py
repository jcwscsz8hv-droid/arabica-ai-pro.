"""Calculate deterministic checksums of generated release files."""
from pathlib import Path
import hashlib
import sys


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(folder: str) -> int:
    directory = Path(folder)
    parts = sorted(p for p in directory.iterdir() if p.is_file() and p.name != "SHA256SUMS.txt")
    if not parts:
        raise SystemExit("Empty release directory")
    manifest = "\n".join(f"{sha256(p)}  {p.name}" for p in parts) + "\n"
    (directory / "SHA256SUMS.txt").write_text(manifest, encoding="utf-8")
    print(f"SHA256SUMS.txt: {len(parts)} files")
    return 0

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
