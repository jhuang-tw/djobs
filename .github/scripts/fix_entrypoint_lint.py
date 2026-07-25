from pathlib import Path

path = Path("src/djobs/entrypoint.py")
content = path.read_text(encoding="utf-8")
content = content.replace(
    "else \"Run 'djobs legacy install-mcp --force' or remove the broken override.\"",
    "else (\n"
    "                            \"Run 'djobs legacy install-mcp --force' or remove \"\n"
    "                            \"the broken override.\"\n"
    "                        )",
)
content = content.replace(
    '"not present (normal for extension or user-level setup; no project file required)"',
    '"not present (normal for extension or user-level setup; "\n'
    '                    "no project file required)"',
)
path.write_text(content, encoding="utf-8")
