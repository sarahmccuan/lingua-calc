from __future__ import annotations

import threading
import webbrowser

import uvicorn

from lingua_calc.config import get_settings


def _check_credentials(s) -> None:
    """Friendly heads-up if AWS credentials look unconfigured, so a non-technical
    user gets a clear pointer to .env instead of a cryptic error mid-run."""
    placeholder = not s.aws_access_key_id or "PASTE_" in (s.aws_access_key_id or "")
    if placeholder:
        print("=" * 70)
        print("  AWS credentials are not set yet.")
        print("  1. Copy '.env.template' to '.env'")
        print("  2. Paste your AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY")
        print("  3. Save and run this again.")
        print("  (The app will still start, but analysis will fail until this is done.)")
        print("=" * 70)


def main() -> None:
    s = get_settings()
    _check_credentials(s)
    url = f"http://{s.host}:{s.port}/"

    if s.open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    uvicorn.run(
        "lingua_calc.app:app",
        host=s.host,
        port=s.port,
        reload=False,
        factory=False,
    )


if __name__ == "__main__":
    main()
