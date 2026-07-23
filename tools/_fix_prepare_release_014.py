from __future__ import annotations

from pathlib import Path

PATH = Path(__file__).with_name("_prepare_release_014.py")


def replace_once(text: str, old: str, new: str) -> str:
    if old in text:
        return text.replace(old, new, 1)
    if new not in text:
        raise SystemExit(f"release generator patch target not found: {old!r}")
    return text


def main() -> None:
    text = PATH.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "            hook_result = install_host_hooks(\n",
        "            passive_hook_result = install_host_hooks(\n",
    )
    text = replace_once(
        text,
        '        hook_status = str(hook_result["status"])\n',
        '        hook_status = str(passive_hook_result["status"])\n',
    )
    text = replace_once(
        text,
        '            "hooks": hook_result,\n',
        '            "hooks": passive_hook_result,\n',
    )
    text = replace_once(
        text,
        "                f\"passive observation adapter {hook_status} at {hook_result['path']}. \"\n",
        "                f\"passive observation adapter {hook_status} at {passive_hook_result['path']}. \"\n",
    )
    text = replace_once(
        text,
        '    combined = "\\n".join((ROOT / path).read_text(encoding="utf-8").lower() for path in current_surfaces)\n',
        '    combined = "\\\\n".join(\n        (ROOT / path).read_text(encoding="utf-8").lower()\n        for path in current_surfaces\n    )\n',
    )
    PATH.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
