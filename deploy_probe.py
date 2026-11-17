#!/usr/bin/env python3
"""One-shot: switch the Pxxl btpp-test project to the probe image and verify.

Run from GitHub Actions (workflow_dispatch). Never prints secret values.
The refresh token rotates on every call, so the new value is written back to
the repo secret immediately (shared with pxxl_watch.py).
"""
import json
import os
import time

import pxxl_watch as w

BASE = w.BASE
PROJ = os.environ.get("PXXL_PROJECT_ID", "proj_5el66e524fbqjyro0hrw")
DOMAIN = os.environ.get("PXXL_DOMAIN", "https://btpp-test.pxxlspace.cv")
IMG = os.environ.get("PROBE_IMAGE", "ghcr.io/btpp03/pxxl-probe-image:latest")
NEW_URL = "pxxl://images/prebuilt/" + IMG
SECRET = os.environ.get("NZ_CLIENT_SECRET", "").strip()
SERVER = os.environ.get("NZ_SERVER", "nezhav2.btppone.hidns.vip").strip()
DEV = os.environ.get("PXXL_DEVICE_ID", "").strip()

log = w.log


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


def main():
    if not SECRET:
        raise SystemExit("NZ_CLIENT_SECRET missing")
    hd = session()

    for p in (f"/cli/projects/{PROJ}", f"/projects/{PROJ}"):
        st, _, b = w.http("GET", BASE + p, headers=hd)
        log("BASELINE", p, st, brief(b))

    # Env vars: the image reads NZ_SERVER / NZ_CLIENT_SECRET at container start.
    vars_ = [
        {"key": "NZ_SERVER", "value": SERVER, "isSecret": False},
        {"key": "NZ_CLIENT_SECRET", "value": SECRET, "isSecret": True},
    ]
    env_ok = False
    for p in (f"/cli/projects/{PROJ}/envs/bulk", f"/projects/{PROJ}/envs/bulk"):
        st, _, b = w.http("POST", BASE + p, {"vars": vars_, "replace": False}, hd)
        log("ENVS", p, st, "applied" if st in (200, 201) else brief(b, 200))
        if st in (200, 201):
            env_ok = True
            break
    if not env_ok:
        log("WARN env vars not applied - the container will exit without them")

    # Which endpoint actually changes the image of an existing project?
    candidates = [
        ("PUT", f"/cli/projects/{PROJ}", {"githubUrl": NEW_URL}),
        ("PUT", f"/projects/{PROJ}", {"githubUrl": NEW_URL}),
        ("PUT", f"/cli/projects/{PROJ}", {"githubUrl": IMG}),
        ("PUT", f"/cli/projects/{PROJ}", {"githubUrl": NEW_URL, "dockerImage": IMG, "port": 80}),
        ("POST", f"/projects/{PROJ}/deployments", {"dockerImage": IMG}),
        ("POST", "/deployments", {"projectId": PROJ, "dockerImage": IMG}),
        ("PUT", f"/projects/{PROJ}/image", {"dockerImage": IMG}),
        ("POST", f"/projects/{PROJ}/update-image", {"dockerImage": IMG}),
    ]
    applied_via = None
    for method, path, body in candidates:
        st, _, b = w.http(method, BASE + path, body, hd)
        log("SET-IMAGE", method, path, st, brief(b, 260))
        st2, _, b2 = w.http("GET", BASE + f"/cli/projects/{PROJ}", headers=hd)
        log("  GET-after", st2, brief(b2, 420))
        if st in (200, 201, 204) and (IMG in b2 or NEW_URL in b2):
            applied_via = (method, path)
            break
    if not applied_via:
        log("IMAGE-NOT-APPLIED: no endpoint accepted the change; stop before redeploy")
        return
    log("IMAGE-SET via", applied_via)

    st, _, b = w.http("POST", BASE + f"/projects/{PROJ}/redeploy", {}, hd)
    log("REDEPLOY", st, brief(b, 260))
    if st not in (200, 201):
        return

    # The old image serves the nginx welcome page; ours serves a plain "ok".
    # Content check is the end-to-end proof that the new image is live.
    for i in range(12):
        time.sleep(20)
        st, _, b = w.http("GET", BASE + "/deployments?limit=5", headers=hd)
        try:
            data = json.loads(b)
            items = data.get("data", data) if isinstance(data, dict) else data
            if isinstance(items, dict):
                items = items.get("deployments") or items.get("items") or []
            mine = [x for x in items if x.get("projectId") == PROJ]
            if mine:
                d = sorted(mine, key=lambda x: x.get("createdAt") or "", reverse=True)[0]
                log("POLL", i, d.get("id"),
                    d.get("deploymentStatus") or d.get("status"),
                    d.get("buildStatus"))
        except Exception as e:
            log("POLL parse", i, repr(e), brief(b, 120))
        st2, _, txt = w.http("GET", DOMAIN, timeout=25)
        log("DOMAIN", i, st2, brief(txt, 100))
        if st2 == 200 and "Welcome to nginx" not in txt:
            log("NEW-IMAGE-LIVE: response is no longer the nginx default page")
            break

    st, _, b = w.http("GET", BASE + f"/cli/projects/{PROJ}/live-logs?tail=80", headers=hd)
    log("LIVE-LOGS", st, brief(b, 1400))


if __name__ == "__main__":
    main()
