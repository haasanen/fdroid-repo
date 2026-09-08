#!/usr/bin/env bash
# One-shot: secrets + push + Pages + first run for haasanen/fdroid-repo.
# Run from anywhere; uses /opt/data/.gh_tok and /opt/data/fdroid_repo.
set -euo pipefail

TOK=$(cat /opt/data/.gh_tok)
API="https://api.github.com"
REPO="haasanen/fdroid-repo"
cd /opt/data/fdroid_repo

echo '== 1. set secrets =='
KS_B64=$(base64 -w0 repo.jks)
STOREPASS=$(python3 -c "print([l.split('=',1)[1].strip() for l in open('keys/passwords.txt') if l.startswith('STORE')][0])")
KEYPASS=$(python3 -c "print([l.split('=',1)[1].strip() for l in open('keys/passwords.txt') if not l.startswith('STORE') and l.strip()][0])")
for pair in "REPO_KEYSTORE_BASE64:$KS_B64" "REPO_STORE_PASS:$STOREPASS" "REPO_KEY_PASS:$KEYPASS"; do
  name="${pair%%:*}"; val="${pair#*:}"
  code=$(curl -s -o /tmp/secret_resp.json -w "%{http_code}" -X PUT \
    -H "Authorization: token $TOK" -H "Accept: application/vnd.github+json" \
    "$API/repos/$REPO/actions/secrets/$name" \
    -d "$(python3 -c "import json,sys;print(json.dumps({'name':sys.argv[1],'data':sys.argv[2]}))" "$name" "$val")")
  echo "  $name -> $code"
done

echo '== 2. push code =='
git init -q -b main 2>/dev/null || git checkout -q -B main
git add -A
git -c user.name='fdroid-repo' -c user.email='fdroid-repo@users.noreply.github.com' \
  commit -qm 'fdroid repo: config, metadata, update+verify scripts, workflow'
git remote add origin "https://x-access-token:$TOK@github.com/$REPO.git" 2>/dev/null || \
  git remote set-url origin "https://x-access-token:$TOK@github.com/$REPO.git"
git push -q -f origin main
echo '  pushed'

echo '== 3. enable GitHub Pages (artifact deploy, main branch) =='
code=$(curl -s -o /tmp/pages_resp.json -w "%{http_code}" -X POST \
  -H "Authorization: token $TOK" -H "Accept: application/vnd.github+json" \
  "$API/repos/$REPO/pages" \
  -d '{"build_type":"workflow","source":{"branch":"main","path":"/"}}')
echo "  pages -> $code"
cat /tmp/pages_resp.json | python3 -c "
import sys,json
d=json.load(sys.stdin)
print('  html_url:', d.get('html_url'), '| status:', d.get('status'), '| source:', d.get('source'))" 2>/dev/null || true

echo '== 4. first run (workflow_dispatch) =='
code=$(curl -s -o /tmp/dispatch_resp.json -w "%{http_code}" -X POST \
  -H "Authorization: token $TOK" -H "Accept: application/vnd.github+json" \
  "$API/repos/$REPO/actions/workflows/update.yml/dispatches" \
  -d '{"ref":"main"}')
echo "  dispatch -> $code"

echo '== done =='
