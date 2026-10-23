"""Download the Kokoro TTS model files (~350 MB). Whisper downloads itself on first run."""
import sys
from pathlib import Path

import httpx

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
FILES = ["kokoro-v1.0.onnx", "voices-v1.0.bin"]


def download(name: str):
    dest = MODELS_DIR / name
    if dest.exists():
        print(f"✓ {name} already present")
        return
    tmp = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream("GET", f"{BASE}/{name}", follow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        done = 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if total:
                    sys.stdout.write(f"\r  {name}: {done * 100 // total}% of {total >> 20} MB")
                    sys.stdout.flush()
    tmp.rename(dest)
    print(f"\r✓ {name} downloaded{' ' * 20}")


if __name__ == "__main__":
    MODELS_DIR.mkdir(exist_ok=True)
    for f in FILES:
        download(f)
