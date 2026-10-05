#!/bin/bash
S=/private/tmp/claude-501/-Users-linhancheng-Desktop-projects/755eec5b-070f-4f5a-8a3c-6e4ef7e827b2/scratchpad/real09
SA=/Users/linhancheng/code/social-info/scripts/local-analysis/session-audit.py
PY=/opt/homebrew/bin/python3
F=(--projects $S/projects --state $S/state --started-at 1970-01-01T00:00:00Z --relay-config $HOME/.cli-proxy-api/config.yaml --keys-file $HOME/.cli-proxy-api/keys.env)
$PY $SA scan "${F[@]}"
for i in $(seq 1 40); do
  out=$($PY $SA run "${F[@]}" 2>>$S/run.err)
  echo "iter $i $out"
  case "$out" in *'"fragments": 0'*) break;; esac
done
$PY $SA status --state $S/state > $S/status.json
$PY $SA report --state $S/state > $S/report.txt
$PY $SA promote --state $S/state --friction-root $S/friction > $S/promote.json
$PY $SA zero-use --state $S/state > $S/zero-use.md
echo done
