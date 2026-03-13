import re
import sys
from pathlib import Path


def clean_log(path: Path) -> None:
    text: str = path.read_text(encoding="utf-8")
    cleaned: str = re.sub(
        r"data:image/[a-zA-Z]+;base64,[A-Za-z0-9+/=]{64,}",
        "BASE64_IMAGE",
        text,
    )
    cleaned = re.sub(
        r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/=]{200,}(?![A-Za-z0-9+/=])",
        "BASE64_DATA",
        cleaned,
    )
    path.write_text(cleaned, encoding="utf-8")
    print(f"cleaned {path}")


if __name__ == "__main__":
    target: Path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("panel.txt")
    if not target.exists():
        print(f"file not found: {target}")
        raise SystemExit(1)
    clean_log(target)
