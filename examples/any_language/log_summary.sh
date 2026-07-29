#!/usr/bin/env bash
# log_summary.sh — count log lines by level and print the 3 most frequent
#                  messages.
# Usage: bash log_summary.sh <logfile>
#
# Expects lines like:
#   2024-03-01 08:00:01 INFO  server started
#   2024-03-01 08:00:02 WARN  disk at 80%
#   2024-03-01 08:00:03 ERROR connection refused

set -euo pipefail

FILE="${1:?Usage: bash log_summary.sh <logfile>}"

echo "=== Log summary: $(basename "$FILE") ==="
echo "Total lines: $(wc -l < "$FILE")"
echo ""
echo "Lines by level:"
awk '{print $3}' "$FILE" \
    | sort | uniq -c | sort -rn \
    | awk '{printf "  %-8s %d\n", $2, $1}'
echo ""
echo "Top 3 messages:"
awk '{$1=$2=$3=""; sub(/^[[:space:]]+/,""); print}' "$FILE" \
    | sort | uniq -c | sort -rn | head -3 \
    | awk '{$1=""; sub(/^[[:space:]]+/,""); printf "  (%d) %s\n", NR, $0}'
