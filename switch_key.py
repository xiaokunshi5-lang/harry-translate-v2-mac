"""Compatibility marker for the retired V1 key utility.

The executable V1 implementation lives in ``legacy/v1/switch_key.py``.
Use ``./启动同传系统.command --legacy`` for the emergency V1 entry.
"""

from pathlib import Path

LEGACY_KEY_SWITCHER = Path(__file__).resolve().parent / "legacy" / "v1" / "switch_key.py"
