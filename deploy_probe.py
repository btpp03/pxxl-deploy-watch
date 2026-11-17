#!/usr/bin/env python3
"""One-shot: deploy the Nezha probe to Pxxl.

Pxxl has no API to change the image of an existing docker-image project, and
SpaceDrop on Free only accepts HTML/PHP. The only working image path is
POST /projects/create/docker-image, which creates a new project `btpp-probe`.
Nothing is deleted; btpp-test is left untouched.

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

    # Capacity probe: the free tier's eu_central pool is often CPU-starved,
    # which is exactly why the original deploy sat in `queued` and then failed.
    st, _, b = w.http("GET", BASE + "/runtime-regions", headers=hd)
    log("REGIONS-HTTP", st)
    if st == 200:
        try:
            d = json.loads(b)
            log("RECOMMENDED", d.get("recommendedRegionId"))
            for r in d.get("regions", []):
                log("REGION", r.get("id"), r.get("location"),
                    "avail=", r.get("availabilityStatus"),
                    "cpuFree=", r.get("freeCpuCores"),
                    "memFreeMb=", r.get("freeMemoryMb"),
                    "healthy=", r.get("healthyServers"), "/", r.get("totalServers"))
        except Exception as e:
            log("REGIONS parse", repr(e), brief(b, 400))
    else:
        log("REGIONS body", brief(b, 400))
    if os.environ.get("PROBE_ONLY") == "1":
        log("PROBE-ONLY: capacity read, no project deleted or created")
        return

    st, _, b = w.http("GET", BASE + f"/cli/projects/{PROJ}", headers=hd)
    log("BASELINE", st, brief(b))
    old_url = ""
    try:
        old_url = json.loads(b)["project"]["githubUrl"]
    except Exception:
        pass

    # Free users cannot pick a region; follow the recommended one.
    rid = None
    st, _, b = w.http("GET", BASE + "/runtime-regions", headers=hd)
    if st == 200:
        try:
            rid = json.loads(b).get("recommendedRegionId")
        except Exception:
            pass
    log("RECOMMENDED-REGION", rid)

    payload = {
        "name": "btpp-probe",
        "dockerImage": IMG,
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
    if st not in (200, 201):
        log("CREATE-FAILED: no project created, nothing else changed")
        return

    target, domain = PROJ, DOMAIN
    try:
        d = json.loads(b)
        new_id = d.get("projectId") or (d.get("project") or {}).get("id")
        new_domain = d.get("domainName")
        if new_id and new_id != PROJ:
            target, domain = new_id, "https://" + new_domain
        log("TARGET", target, domain)
    except Exception as e:
        log("CREATE parse", repr(e))

    # Did projectId replace the old image in place?
    st2, _, b2 = w.http("GET", BASE + f"/cli/projects/{PROJ}", headers=hd)
    try:
        now_url = json.loads(b2)["project"]["githubUrl"]
    except Exception:
        now_url = ""
    log("OLD-PROJECT-IMAGE", old_url, "->", now_url)
    if target == PROJ and IMG not in now_url:
        log("NOTE projectId was ignored on the existing project; treating as new project")
    push_envs(hd, target)

    # The old image serves the nginx welcome page; ours serves a plain "ok".
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
            log("PROBE-LIVE: response is not the nginx default page")
            break

    st, _, b = w.http("GET", BASE + f"/cli/projects/{target}/live-logs?tail=80", headers=hd)
    log("LIVE-LOGS", st, brief(b, 1600))


if __name__ == "__main__":
    main()
