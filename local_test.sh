#!/bin/bash
# 本地模拟 GA 环境跑一次 pxxl_watch.py（TG 置空避免打扰）
set -euo pipefail
cd /root/pxxl-watch
eval "$(python3 - <<'PY'
import http.cookiejar
jar = http.cookiejar.MozillaCookieJar("/root/.pxxl_jar")
jar.load(ignore_discard=True, ignore_expires=True)
c = {x.name: x.value for x in jar}
print(f'export PXXL_REFRESH="{c["pxxl_refresh"]}"')
print(f'export PXXL_CSRF="{c["pxxl_csrf"]}"')
print(f'export PXXL_SIGNUP_DEVICE="{c.get("pxxl_signup_device","")}"')
print(f'export PXXL_DEVICE_ID="{open("/root/.pxxl_visitor.txt").read().strip()}"')
PY
)"
export GH_PAT="$(cat /root/.gh_tok)"
export GITHUB_REPOSITORY=btpp03/pxxl-deploy-watch
export TG_BOT_TOKEN=""
export TG_CHAT_ID=""
export PXXL_PROJECT_ID=proj_5el66e524fbqjyro0hrw
export PXXL_DOMAIN=https://btpp-test.pxxlspace.cv
timeout 120 python3 pxxl_watch.py
