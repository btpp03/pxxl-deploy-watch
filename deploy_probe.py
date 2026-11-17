#!/usr/bin/env python3
"""Replace the placeholder btpp-test project with the Nezha probe, safely.

Pxxl Free allows exactly 1 web service project and has no API to change an
existing docker-image project's image. So: verify the free CPU pool, delete the
nginx placeholder, immediately re-create a project with the SAME name/domain
pointing at the probe image, push env vars, and verify end to end.

Safety:
  * PROBE_ONLY=1 -> read capacity only, change nothing.
  * CPU gate: recommended region must have >= 0.5 free vCPU before deleting.
  * Rollback: if create fails, re-create the original nginx:alpine placeholder.
  * Delete and create run back-to-back in one GA job.

Run from GitHub Actions (workflow_dispatch). Never prints secret values.
"""
import json
import os
import time

import pxxl_watch as w

BASE = w.BASE
PROJ = os.environ.get("PXXL_PROJECT_ID", "proj_5el66e524fbqjyro0hrw")
DOMAIN = os.environ.get("PXXL_DOMAIN", "https://btpp-test.pxxlspace.cv")
IMG = os.environ.get("PROBE_IMAGE", "ghcr.io/btpp03/pxxl-probe-image:latest")
PLACEHOLDER = "docker.io/library/nginx:alpine"
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


def capacity(hd):
    st, _, b = w.http("GET", BASE + "/runtime-regions", headers=hd)
    log("REGIONS-HTTP", st)
    rid, cpu, mem = None, 0.0, 0
    if st == 200:
        try:
            d = json.loads(b)
            rid = d.get("recommendedRegionId")
            log("RECOMMENDED", rid)
            for r in d.get("regions", []):
                log("REGION", r.get("id"), r.get("location"),
                    "avail=", r.get("availabilityStatus"),
                    "cpuFree=", r.get("freeCpuCores"),
                    "memFreeMb=", r.get("freeMemoryMb"),
                    "healthy=", r.get("healthyServers"), "/", r.get("totalServers"))
                if r.get("id") == rid:
                    cpu = float(r.get("freeCpuCores") or 0)
                    mem = int(r.get("freeMemoryMb") or 0)
        except Exception as e:
            log("REGIONS parse", repr(e), brief(b, 400))
    else:
        log("REGIONS body", brief(b, 400))
    return rid, cpu, mem


def create(hd, image, rid):
    payload = {
        "name": "btpp-test",
        "dockerImage": image,
        "env": "",
        "port": "80",
        "environment": "production",
        "label": "Work",
        "domainChoice": "pxxlspace.cv",
        "teamId": None,
    }
    if rid:
        payload["regionId"] = rid
    st, _, b = w.http("POST", BASE + "/projects/create/docker-image", payload, hd)
    log("CREATE", st, brief(b, 700))
    return st, b


def push_envs(hd, project_id):
    vars_ = [
        {"key": "NZ_SERVER", "value": SERVER, "isSecret": False},
        {"key": "NZ_CLIENT_SECRET", "value": SECRET, "isSecret": True},
    ]
    st, _, b = w.http("POST", BASE + f"/cli/projects/{project_id}/envs/bulk",
                      {"vars": vars_, "replace": False}, hd)
    log("ENVS", project_id, st, "applied" if st in (200, 201) else brief(b, 200))
    return st in (200, 201)


def main():
    if not SECRET:
        raise SystemExit("NZ_CLIENT_SECRET missing")
    hd = session()

    rid, cpu, mem = capacity(hd)
    log("GATE", "region=", rid, "cpuFree=", cpu, "memFreeMb=", mem)
    if os.environ.get("PROBE_ONLY") == "1":
        log("PROBE-ONLY: capacity read, no project deleted or created")
        return
    if cpu < 0.5:
        log("GATE-BLOCK: no free CPU in the recommended region; nothing deleted")
        return

    # 1) release the only web-service slot
    st, _, b = w.http("DELETE", BASE + f"/cli/projects/{PROJ}", None, hd)
    log("DELETE", st, brief(b, 300))
    if st not in (200, 204):
        st, _, b = w.http("DELETE", BASE + f"/projects/{PROJ}", None, hd)
        log("DELETE-alt", st, brief(b, 300))
    if st not in (200, 204):
        log("DELETE-FAILED: aborting, old project untouched")
        return

    # 2) immediately re-create with the same name/domain
    st, b = create(hd, IMG, rid)
    if st not in (200, 201):
        st2, b2 = create(hd, PLACEHOLDER, rid)
        log("ROLLBACK", st2, brief(b2, 400))
        log("RESTORE-RESULT", "original nginx placeholder restored"
            if st2 in (200, 201) else "RESTORE FAILED - check dashboard manually")
        return

    target, domain = PROJ, DOMAIN
    try:
        d = json.loads(b)
        target = d.get("projectId") or (d.get("project") or {}).get("id") or PROJ
        dn = d.get("domainName")
        if dn:
            domain = "https://" + dn
    except Exception as e:
        log("CREATE parse", repr(e))
    log("NEW-PROJECT-ID", target, "DOMAIN", domain)
    push_envs(hd, target)

    # The old image served the nginx welcome page; ours serves a plain "ok".
    for i in range(12):
        time.sleep(20)
        st, _, b = w.http("GET", BASE + "/deployments?limit=8", headers=hd)
        try:
            d = json.loads(b)
            items = d.get("data", d) if isinstance(d, dict) else d
            if isinstance(items, dict):
                items = items.get("deployments") or items.get("items") or []
            mine = [x for x in items if x.get("projectId") == target]
            if mine:
                x = sorted(mine, key=lambda y: y.get("createdAt") or "", reverse=True)[0]
                log("POLL", i, x.get("id"),
                    x.get("deploymentStatus") or x.get("status"), x.get("buildStatus"))
        except Exception as e:
            log("POLL parse", i, repr(e))
        st2, _, txt = w.http("GET", domain, timeout=25)
        log("DOMAIN", i, st2, brief(txt, 100))
        if st2 == 200 and "Welcome to nginx" not in txt:
            log("PROBE-LIVE: response is no longer the nginx default page")
            break

    st, _, b = w.http("GET", BASE + f"/cli/projects/{target}/live-logs?tail=80", headers=hd)
    log("LIVE-LOGS", st, brief(b, 1600))


if __name__ == "__main__":
    main()
