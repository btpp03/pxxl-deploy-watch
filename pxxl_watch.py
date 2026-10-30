#!/usr/bin/env python3
"""Pxxl 免费层部署看门狗。

每次跑：刷新 access token（refresh token 会轮换，刷新后立刻写回 repo secret）→
查最新部署状态 → 成功则 curl 域名验证并推 TG → 失败/排队过久则重新触发部署。
默认静默：没排上就不发消息（只写日志）。
"""
import base64
import json
import os
import time
import urllib.error
import urllib.request

BASE = "https://server.pxxl.app/api/v3"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
PROJ = os.environ.get("PXXL_PROJECT_ID", "proj_5el66e524fbqjyro0hrw")
DOMAIN = os.environ.get("PXXL_DOMAIN", "https://btpp-test.pxxlspace.cv")
REPO = os.environ.get("GITHUB_REPOSITORY", "btpp03/pxxl-deploy-watch")
STALE_HOURS = 12

RT = os.environ.get("PXXL_REFRESH", "").strip()
DEV = os.environ.get("PXXL_DEVICE_ID", "").strip()
PAT = os.environ.get("GH_PAT", "").strip()
TG_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT = os.environ.get("TG_CHAT_ID", "").strip()

OK_STATES = {"running", "live", "deployed", "active", "success", "succeeded", "completed"}
BAD_STATES = {"failed", "error", "cancelled", "canceled"}


def log(*a):
    print(*a, flush=True)


def http(method, url, body=None, headers=None, timeout=40):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    h = {"User-Agent": UA, "Accept": "application/json"}
    if body is not None:
        h["Content-Type"] = "application/json"
    if headers:
        h.update(headers)
    for k, v in h.items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.headers, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, {}, repr(e)


def gh_api(path, method="GET", body=None):
    return http(method, "https://api.github.com" + path, body,
                {"Authorization": "token " + PAT,
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"})


def put_secret(name, value):
    st, _, b = gh_api(f"/repos/{REPO}/actions/secrets/public-key")
    if st != 200:
        log("secret public-key failed", st, b[:200])
        return False
    pk = json.loads(b)
    from nacl import encoding, public
    sealed = public.SealedBox(public.PublicKey(pk["key"], encoding.Base64Encoder)).encrypt(value.encode())
    st, _, b = gh_api(f"/repos/{REPO}/actions/secrets/{name}", "PUT",
                      {"encrypted_value": base64.b64encode(sealed).decode(), "key_id": pk["key_id"]})
    log("secret", name, "updated" if st in (201, 204) else f"failed {st} {b[:120]}")
    return st in (201, 204)


def get_var(name):
    st, _, b = gh_api(f"/repos/{REPO}/actions/variables/{name}")
    if st != 200:
        return None
    try:
        return json.loads(b).get("value")
    except Exception:
        return None


def set_var(name, value):
    st, _, b = gh_api(f"/repos/{REPO}/actions/variables/{name}", "PATCH", {"name": name, "value": value})
    if st == 404:
        st, _, b = gh_api(f"/repos/{REPO}/actions/variables", "POST", {"name": name, "value": value})
    log("var", name, "->", value, st)


def notify(text):
    if not (TG_TOKEN and TG_CHAT):
        log("no TG creds, skip notify")
        return
    st, _, b = http("POST", f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                    {"chat_id": TG_CHAT, "text": text, "disable_web_page_preview": True})
    log("tg", st, b[:160])


def cookie_val(headers, name):
    for c in headers.get_all("Set-Cookie") or []:
        first = c.split(";", 1)[0]
        if "=" in first:
            k, v = first.split("=", 1)
            if k.strip() == name:
                return v.strip()
    return None


def main():
    if get_var("PXXL_DONE") == "1":
        log("already done, skip")
        return

    # 1) 刷新 token（会轮换 refresh token，立刻写回 secret）
    st, hdrs, body = http("POST", f"{BASE}/auth/refresh-token", {}, {"Cookie": f"pxxl_refresh={RT}"})
    log("refresh", st, body[:120])
    if st != 200:
        notify(f"Pxxl 看门狗：refresh token 失效（HTTP {st}），需人工重新登录。")
        return

    access = cookie_val(hdrs, "pxxl_access") or ""
    refresh = cookie_val(hdrs, "pxxl_refresh") or ""
    csrf = cookie_val(hdrs, "pxxl_csrf") or ""
    try:
        csrf = json.loads(body).get("csrfToken") or csrf
    except Exception:
        pass
    if refresh and refresh != RT:
        put_secret("PXXL_REFRESH", refresh)

    ck = f"pxxl_access={access}; pxxl_refresh={refresh or RT}; pxxl_csrf={csrf}"
    hd = {"Cookie": ck, "Origin": "https://app.pxxl.app",
          "Referer": "https://app.pxxl.app/", "X-Device-ID": DEV, "X-CSRF-Token": csrf}

    # 2) 查最新部署
    st, _, b = http("GET", f"{BASE}/deployments?limit=10", headers=hd)
    log("deployments", st, b[:200])
    dep = None
    if st == 200:
        try:
            data = json.loads(b)
            items = data.get("data", data) if isinstance(data, dict) else data
            if isinstance(items, dict):
                items = items.get("deployments") or items.get("items") or []
            mine = [x for x in items if x.get("projectId") == PROJ]
            if mine:
                dep = sorted(mine, key=lambda x: x.get("createdAt") or "", reverse=True)[0]
        except Exception as e:
            log("parse deployments failed", e)

    if not dep:
        log("no deployment found")
        return

    did = dep.get("id") or dep.get("deploymentId")
    status = (dep.get("deploymentStatus") or dep.get("status") or "").lower()
    build = (dep.get("buildStatus") or "").lower()
    created = dep.get("createdAt") or ""
    log("latest", did, "dep=", status, "build=", build, "created=", created)

    # 3) 成功 → 实测域名
    if status in OK_STATES:
        st2, _, txt = http("GET", DOMAIN, timeout=25)
        log("domain http", st2, txt[:80])
        if st2 and 200 <= st2 < 400:
            notify(f"✅ Pxxl 免费层排上了\n{did} 状态 {status}\n{DOMAIN} → HTTP {st2}\n镜像 nginx:alpine，区域 eu_central")
            set_var("PXXL_DONE", "1")
        else:
            log("deployment says running but domain not serving yet")
        return

    # 4) 失败/卡太久 → 重新触发
    age_h = None
    try:
        from datetime import datetime, timezone
        t = datetime.fromisoformat(created.replace("Z", "+00:00"))
        age_h = (datetime.now(timezone.utc) - t).total_seconds() / 3600
    except Exception:
        pass
    log("age_hours", age_h)

    if status in BAD_STATES or (age_h is not None and age_h > STALE_HOURS):
        st, _, b = http("POST", f"{BASE}/projects/{PROJ}/redeploy", {}, hd)
        log("redeploy", st, b[:200])
        if st in (200, 201):
            try:
                log("new deployment", json.loads(b).get("deploymentId"))
            except Exception:
                pass


if __name__ == "__main__":
    main()
