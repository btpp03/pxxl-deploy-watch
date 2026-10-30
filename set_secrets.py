import base64
import http.cookiejar
import json
import re
import urllib.request

from nacl import encoding, public

PAT = open("/root/.gh_tok").read().strip()
REPO = "btpp03/pxxl-deploy-watch"
H = {"Authorization": f"token {PAT}", "Accept": "application/vnd.github+json",
     "X-GitHub-Api-Version": "2022-11-28"}

jar = http.cookiejar.MozillaCookieJar("/root/.pxxl_jar")
jar.load(ignore_discard=True, ignore_expires=True)
cookies = {c.name: c.value for c in jar}
RT = cookies["pxxl_refresh"]
DEV = open("/root/.pxxl_visitor.txt").read().strip()

doc = open("/root/.hermes/skills/z-image-generation/references/tg-bot-dual-channel.md",
           errors="replace").read()
BOT = re.search(r'(\d{8,11}:[A-Za-z0-9_-]{30,40})', doc).group(1)
CHAT = re.search(r'chat_id\s*=\s*`(\d+)`', doc).group(1)
print("bot id:", BOT.split(":")[0], "| chat:", CHAT, "| rt len:", len(RT))


def api(url, data=None, method="GET"):
    b = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(url, data=b, headers=H, method=method)
    with urllib.request.urlopen(r, timeout=30) as resp:
        body = resp.read().decode()
        return json.loads(body) if body else {}


pk = api(f"https://api.github.com/repos/{REPO}/actions/secrets/public-key")
for name, val in [("PXXL_REFRESH", RT), ("PXXL_DEVICE_ID", DEV), ("GH_PAT", PAT),
                  ("TG_BOT_TOKEN", BOT), ("TG_CHAT_ID", CHAT)]:
    sealed = public.SealedBox(public.PublicKey(pk["key"], encoding.Base64Encoder)).encrypt(val.encode())
    api(f"https://api.github.com/repos/{REPO}/actions/secrets/{name}",
        {"encrypted_value": base64.b64encode(sealed).decode(), "key_id": pk["key_id"]}, method="PUT")
    print("set", name)

print("secrets:", [s["name"] for s in api(f"https://api.github.com/repos/{REPO}/actions/secrets").get("secrets", [])])
