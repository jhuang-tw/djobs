#!/usr/bin/env bash
# Record a ~30 second demo GIF using asciinema + agg.
#
# Prerequisites:
#   pip install asciinema
#   cargo install --git https://github.com/asciinema/agg   (or download binary)
#
# Usage:
#   bash scripts/record_demo.sh          # record .cast file
#   agg demo.cast demo.gif --cols 80 --rows 24 --speed 2
#
# The resulting demo.gif can be embedded at the top of README.md.

set -euo pipefail

CAST_FILE="demo.cast"
DEMO_CMD="python examples/legacy_queue/run_migration_demo.py"

echo "Recording demo to ${CAST_FILE}..."
echo "Press Ctrl-D or exit when done."

asciinema rec \
  --cols 80 \
  --rows 24 \
  --idle-time-limit 1 \
  --command "${DEMO_CMD}" \
  "${CAST_FILE}"

echo ""
echo "Done. Convert to GIF with:"
echo "  agg ${CAST_FILE} demo.gif --cols 80 --rows 24 --speed 2"
