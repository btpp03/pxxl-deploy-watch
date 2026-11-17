#!/usr/bin/env python3
"""One-shot: deploy the Nezha probe to the EXISTING btpp-test project.

Pxxl has no API to change the image of an existing docker-image project, but
POST /projects/spacedrop accepts `projectId` and creates a new deployment for
it. We upload a tiny Node web service that (a) answers the platform health
check on $PORT and (b) runs the official Nezha v2 agent as an outbound client.

Run from GitHub Actions (workflow_dispatch). Never prints secret values.
"""
import io
import json
import os
import time
import urllib.error
import urllib.request
import uuid
import zipfile

import pxxl_watch as w

BASE = w.BASE
PROJ = os.environ.get("PXXL_PROJECT_ID", "proj_5el66e524fbqjyro0hrw")
DOMAIN = os.environ.get("PXXL_DOMAIN", "https://btpp-test.pxxlspace.cv")
SECRET = os.environ.get("NZ_CLIENT_SECRET", "").strip()
SERVER = os.environ.get("NZ_SERVER", "nezhav2.btppone.hidns.vip").strip()
DEV = os.environ.get("PXXL_DEVICE_ID", "").strip()

log = w.log

SERVER_JS = r"""const http = require('http');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { spawn } = require('child_process');

const SECRET = process.env.NZ_CLIENT_SECRET || '';
let SERVER = process.env.NZ_SERVER || 'nezhav2.btppone.hidns.vip';
if (!/:\d+$/.test(SERVER)) SERVER += ':443';
const PORT = process.env.PORT || 80;
if (!SECRET) { console.error('FATAL: NZ_CLIENT_SECRET missing'); process.exit(1); }

const h = crypto.createHash('sha256').update(SECRET).digest('hex');
const UUID = process.env.NZ_UUID ||
  h.slice(0,8) + '-' + h.slice(8,12) + '-4' + h.slice(12,15) +
  '-a' + h.slice(15,18) + '-' + h.slice(18,30);

const cfg = '/tmp/nz-config.yml';
fs.writeFileSync(cfg, [
  'client_secret: ' + SECRET,
  'debug: true',
  'disable_auto_update: true',
  'disable_command_execute: true',
  'disable_force_update: true',
  'insecure_tls: false',
  'report_delay: 3',
  'server: ' + SERVER,
  'tls: true',
  'uuid: ' + UUID,
  ''
].join('\n'), { mode: 0o600 });

const arch = process.arch === 'arm64' ? 'arm64' : 'amd64';
const bin = path.join(__dirname, 'bin', 'nezha-agent-' + arch);
try { fs.chmodSync(bin, 0o755); } catch (e) { console.error('chmod', e.message); }

// The agent lets NZ_* env vars override config.yml scalars; drop them so the
// normalized config (with :443) stays authoritative.
const clean = {};
for (const k of Object.keys(process.env)) {
  if (!k.startsWith('NZ_')) clean[k] = process.env[k];
}

function runAgent() {
  console.log('[probe] agent start -> ' + SERVER);
  const p = spawn(bin, ['-c', cfg], { env: clean, stdio: 'inherit' });
  p.on('error', e => console.error('[probe] spawn error', e.message));
  p.on('exit', () => setTimeout(runAgent, 30000));
}
runAgent();

http.createServer((req, res) => {
  res.writeHead(200, { 'Content-Type': 'text/plain' });
  res.end('ok\n');
}).listen(PORT, '0.0.0.0', () => console.log('[probe] health responder on ' + PORT));
"""

PACKAGE_JSON = json.dumps({
    "name": "pxxl-probe", "version": "1.0.0", "private": True,
    "scripts": {"start": "node server.js"},
}, indent=2)


def session():
    rt = os.environ.get("PXXL_REFRESH", "").strip()
    csrf0 = os.environ.get("PXXL_CSRF", "").strip()
    sdev = os.environ.get("PXXL_SIGNUP_DEVICE", "").strip()
    cookie = f"pxxl_refresh={rt}; pxxl_csrf={csrf0}"
    if sdev:
        cookie += f"; pxxl_signup_device={sdev}"
    st, hdrs, body = w.http(
        "POST", f"{BASE}/auth/refresh-token", {},
        {"Cookie": cookie, "X-CSRF-Token": csrf0,
         "Origin": "https://app.pxxl.app", "Referer": "https://app.pxxl.app/"})
    log("refresh", st, body[:120])
    if st != 200:
        raise SystemExit("refresh failed")
    access = w.cookie_val(hdrs, "pxxl_access") or ""
    refresh = w.cookie_val(hdrs, "pxxl_refresh") or ""
    csrf = w.cookie_val(hdrs, "pxxl_csrf") or csrf0
    try:
        csrf = json.loads(body).get("csrfToken") or csrf
    except Exception:
        pass
    if refresh and refresh != rt:
        w.put_secret("PXXL_REFRESH", refresh)
    if csrf and csrf != csrf0:
        w.put_secret("PXXL_CSRF", csrf)
    return {"Cookie": f"pxxl_access={access}; pxxl_refresh={refresh or rt}; pxxl_csrf={csrf}",
            "Origin": "https://app.pxxl.app", "Referer": "https://app.pxxl.app/",
            "X-Device-ID": DEV, "X-CSRF-Token": csrf}


def brief(b, n=420):
    return b[:n].replace("\n", " ")


def download(url):
    req = urllib.request.Request(url, headers={"User-Agent": w.UA})
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.read()


def build_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("package.json", PACKAGE_JSON)
        z.writestr("server.js", SERVER_JS)
        for arch in ("amd64", "arm64"):
            url = ("https://github.com/nezhahq/agent/releases/latest/download/"
                   f"nezha-agent_linux_{arch}.zip")
            raw = download(url)
            with zipfile.ZipFile(io.BytesIO(raw)) as az:
                binary = az.read("nezha-agent")
            info = zipfile.ZipInfo(f"bin/nezha-agent-{arch}")
            info.external_attr = 0o755 << 16
            z.writestr(info, binary)
            log("zip binary", arch, len(binary))
    return buf.getvalue()


def post_multipart(url, fields, file_data, hd):
    boundary = "----pxxl" + uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
        'filename="probe.zip"\r\nContent-Type: application/zip\r\n\r\n'.encode()
        + file_data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    r = urllib.request.Request(url, data=body, method="POST")
    h = {"User-Agent": w.UA, "Accept": "application/json",
         "Content-Type": f"multipart/form-data; boundary={boundary}"}
    h.update(hd)
    for k, v in h.items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=180) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def main():
    if not SECRET:
        raise SystemExit("NZ_CLIENT_SECRET missing")
    hd = session()

    vars_ = [
        {"key": "NZ_SERVER", "value": SERVER, "isSecret": False},
        {"key": "NZ_CLIENT_SECRET", "value": SECRET, "isSecret": True},
    ]
    st, _, b = w.http("POST", BASE + f"/cli/projects/{PROJ}/envs/bulk",
                      {"vars": vars_, "replace": False}, hd)
    log("ENVS", st, "applied" if st in (200, 201) else brief(b, 200))

    data = build_zip()
    log("ZIP bytes", len(data))
    fields = {
        "projectId": PROJ,
        "port": "80",
        "environment": "production",
        "startCommand": "node server.js",
        "commitMessage": "nezha probe (health responder + official agent)",
    }
    st, b = post_multipart(BASE + "/projects/spacedrop", fields, data, hd)
    log("SPACEDROP", st, brief(b, 500))
    if st not in (200, 201):
        log("SPACEDROP-FAILED: no deployment created; nothing else changed")
        return

    # The old image serves the nginx welcome page; ours serves a plain "ok".
    for i in range(12):
        time.sleep(20)
        st, _, b = w.http("GET", BASE + "/deployments?limit=5", headers=hd)
        try:
            d = json.loads(b)
            items = d.get("data", d) if isinstance(d, dict) else d
            if isinstance(items, dict):
                items = items.get("deployments") or items.get("items") or []
            mine = [x for x in items if x.get("projectId") == PROJ]
            if mine:
                x = sorted(mine, key=lambda y: y.get("createdAt") or "", reverse=True)[0]
                log("POLL", i, x.get("id"),
                    x.get("deploymentStatus") or x.get("status"), x.get("buildStatus"))
        except Exception as e:
            log("POLL parse", i, repr(e))
        st2, _, txt = w.http("GET", DOMAIN, timeout=25)
        log("DOMAIN", i, st2, brief(txt, 100))
        if st2 == 200 and "Welcome to nginx" not in txt:
            log("PROBE-LIVE: response is no longer the nginx default page")
            break

    st, _, b = w.http("GET", BASE + f"/cli/projects/{PROJ}/live-logs?tail=80", headers=hd)
    log("LIVE-LOGS", st, brief(b, 1600))


if __name__ == "__main__":
    main()
