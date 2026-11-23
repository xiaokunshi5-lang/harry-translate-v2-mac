"""Compatibility marker for the retired V1 server.

The executable V1 implementation lives in ``legacy/v1/server.py`` and is
started only through ``启动同传系统.command --legacy``.
"""

from pathlib import Path

LEGACY_SERVER = Path(__file__).resolve().parent / "legacy" / "v1" / "server.py"


if __name__ == "__main__":
    raise SystemExit(
        f"V1 implementation moved to {LEGACY_SERVER}. "
        "Use ./启动同传系统.command --legacy."
    )
