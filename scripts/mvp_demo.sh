#!/usr/bin/env bash
# MVP one-command demo: natural-language request -> approval gate -> single-source
# retrieval -> deterministic screening -> report.
#
# Usage:
#   scripts/mvp_demo.sh                          # offline fixture (no network)
#   scripts/mvp_demo.sh mc3d                     # real Materials Cloud MC3D (no key)
#   scripts/mvp_demo.sh nomad                    # real NOMAD (no key; can be slow)
#   MP_API_KEY=... scripts/mvp_demo.sh materials_project   # real MP (full verdicts)
set -euo pipefail
cd "$(dirname "$0")/.."

SOURCE="${1:-offline}"
WS="${MVP_WORKSPACE:-/tmp/ma-mvp-demo}"
TS="$(date +%s)"
RUN="mvp-${SOURCE}-${TS}"
REQUEST="寻找同时包含 Si 和 O、带隙为 0.5–1.0 eV、energy above hull 不超过 0.05 eV/atom 的非金属材料。"
CLI=".venv/bin/material-agent"

"$CLI" project create --workspace "$WS" --project-id mvp >/dev/null 2>&1 || true

if [ "$SOURCE" = "offline" ]; then
  "$CLI" run --workspace "$WS" --project mvp --run-id "$RUN" \
    --source materials_project --request "$REQUEST" \
    --fixture tests/fixtures/mp-summary.si-o.json >/dev/null
else
  "$CLI" run --workspace "$WS" --project mvp --run-id "$RUN" \
    --source "$SOURCE" --request "$REQUEST" >/dev/null
fi

APPROVAL="$("$CLI" status --workspace "$WS" --project mvp --run "$RUN" \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["interrupts"][0]["value"]["approval_id"])')"

echo "== Requirement gate reached (approval: $APPROVAL)."
echo "== Request: $REQUEST"
read -r -p "== Press Enter to APPROVE and run retrieval (Ctrl-C to abort) "

"$CLI" approve --workspace "$WS" --project mvp --run "$RUN" \
  --approval "$APPROVAL" --decision approve >/dev/null

"$CLI" report --workspace "$WS" --project mvp --run "$RUN" >/dev/null 2>&1 || true

REPORT="$WS/mvp/reports/$RUN/report.md"
echo "== Done. Report: $REPORT"
echo "----------------------------------------"
sed -n '1,30p' "$REPORT"
