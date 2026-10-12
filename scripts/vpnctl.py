#!/usr/bin/env python3
"""Client profiles, subscriptions and the ru-entry panel config, all built from the vault.
Run from WSL in the repo root.

  scripts/vpnctl.py links [NAME...]   subscription URL + vless:// links into out/<name>.txt
  scripts/vpnctl.py sub NAME          base64 subscription body for NAME (stdout; used by Ansible)
  scripts/vpnctl.py client PROFILE    Xray client config for the `check` user (stdout);
                                      PROFILE: xhttp | vision | fallback | link (link: ru-entry only)
  scripts/vpnctl.py panel-test        ru-entry inbounds/outbound/routing as one Xray config (stdout)
  scripts/vpnctl.py panel-apply       apply them to 3x-ui through its API over an SSH tunnel
"""
import base64
import json
import os
import shlex
import subprocess
import sys
import urllib.parse

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VAULT = os.path.join(ROOT, "inventory/group_vars/all/vault.yml")
VAULT_PASS = os.path.expanduser("~/.config/vpn/vault_pass")
VARS = os.path.join(ROOT, "inventory/group_vars/all/vars.yml")
NL_VARS = os.path.join(ROOT, "inventory/host_vars/nl-exit.yml")

# Profile order = order in the subscription; the first one is the default in client apps.
SUB_PROFILES = [("xhttp", "Основной"), ("vision", "Запасной"), ("fallback", "Резерв (NL)")]
VISION_TAG, XHTTP_TAG = "inbound-443", "inbound-xhttp"
# Xray's own resolver on ru-entry (routing resolves domains for geoip:ru); Google DNS is
# blocked from time to time in Russia.
RU_DNS = {"servers": ["77.88.8.8", "77.88.8.1", "localhost"], "queryStrategy": "UseIPv4"}


def load():
    plain = subprocess.run(
        ["ansible-vault", "view", "--vault-password-file", VAULT_PASS, VAULT],
        check=True, capture_output=True, text=True,
    ).stdout
    v = yaml.safe_load(plain)
    for path in (VARS, NL_VARS):
        with open(path, encoding="utf-8") as f:
            v.update(yaml.safe_load(f))
    return v


def family(v):
    return v.get("vault_family") or []


def profile(v, name, uuid=None):
    """Connection parameters of one client profile (or of the ru-entry -> nl-exit link)."""
    fp, xmux = v["client_fingerprint"], v["xhttp_xmux"]
    ru = {"sni": v["vault_ru_reality_server_name"], "pbk": v["vault_ru_reality_public_key"],
          "sid": v["vault_ru_short_id"]}
    if name == "xhttp":
        return {"address": v["vault_ru_ip"], "port": 443, "uuid": uuid, "flow": "", "network": "xhttp",
                "path": v["vault_ru_xhttp_path"], "xmux": xmux, "fp": fp, **ru}
    if name == "vision":
        return {"address": v["vault_ru_ip"], "port": 443, "uuid": uuid, "flow": "xtls-rprx-vision",
                "network": "tcp", "fp": fp, **ru}
    if name == "fallback":
        return {"address": v["vault_nl_ip"], "port": 443, "uuid": uuid, "flow": "", "network": "xhttp",
                "path": v["vault_fallback_path"], "xmux": xmux, "fp": fp,
                "sni": v["fallback_reality_target"], "pbk": v["vault_fallback_reality_public_key"],
                "sid": v["vault_fallback_short_id"]}
    if name == "link":
        return {"address": v["vault_nl_ip"], "port": int(v["vault_link_port"]), "uuid": v["vault_link_uuid"],
                "flow": "", "network": "xhttp", "path": v["vault_link_path"], "xmux": xmux, "fp": fp,
                "sni": v["link_reality_target"], "pbk": v["vault_link_reality_public_key"],
                "sid": v["vault_link_short_id"]}
    sys.exit(f"unknown profile {name}")


def to_link(p, label):
    q = {"encryption": "none"}
    if p["flow"]:
        q["flow"] = p["flow"]
    q["type"] = p["network"]
    if p["network"] == "xhttp":
        q.update({"path": p["path"], "mode": "auto", "extra": json.dumps({"xmux": p["xmux"]}, separators=(",", ":"))})
    q.update({"security": "reality", "sni": p["sni"], "fp": p["fp"], "pbk": p["pbk"], "sid": p["sid"]})
    return (f"vless://{p['uuid']}@{p['address']}:{p['port']}?{urllib.parse.urlencode(q, quote_via=urllib.parse.quote)}"
            f"#{urllib.parse.quote(label)}")


def to_outbound(p, tag="proxy"):
    user = {"id": p["uuid"], "encryption": "none"}
    if p["flow"]:
        user["flow"] = p["flow"]
    stream = {"network": p["network"], "security": "reality",
              "realitySettings": {"serverName": p["sni"], "fingerprint": p["fp"],
                                  "publicKey": p["pbk"], "shortId": p["sid"]}}
    if p["network"] == "xhttp":
        stream["xhttpSettings"] = {"path": p["path"], "mode": "auto", "xmux": p["xmux"]}
    return {"tag": tag, "protocol": "vless",
            "settings": {"vnext": [{"address": p["address"], "port": p["port"], "users": [user]}]},
            "streamSettings": stream}


def person(v, name):
    for m in family(v):
        if m["name"] == name:
            return m
    sys.exit(f"{name} is not in vault_family")


def sub_body(v, m):
    links = [to_link(profile(v, prof, m["uuid"]), label) for prof, label in SUB_PROFILES]
    return base64.b64encode("\n".join(links).encode()).decode()


def sub(name):
    v = load()
    sys.stdout.write(sub_body(v, person(v, name)))


def links(names):
    v = load()
    people = [m for m in family(v) if not names or m["name"] in names]
    if not people:
        sys.exit("no matching people in vault_family")
    out = os.path.join(ROOT, "out")
    os.makedirs(out, mode=0o700, exist_ok=True)
    for m in people:
        path = os.path.join(out, f"{m['name']}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"https://{v['vault_ru_reality_server_name']}/s/{m['sub']}\n\n")
            for prof, label in SUB_PROFILES:
                f.write(to_link(profile(v, prof, m["uuid"]), f"{m['name']} — {label}") + "\n")
        os.chmod(path, 0o600)
        print(path)


def client(name):
    v = load()
    p = profile(v, name, v["vault_check_uuid"])
    port = 10809 if name == "link" else 10808
    json.dump({
        "log": {"loglevel": "warning"},
        "inbounds": [{"listen": "127.0.0.1", "port": port, "protocol": "socks", "settings": {"udp": False}}],
        "outbounds": [to_outbound(p)],
    }, sys.stdout, indent=2)


def panel_parts(v):
    """What ru-entry's 3x-ui must contain: two inbounds sharing 443, the link outbound, routing."""
    sniffing = {"enabled": True, "destOverride": ["http", "tls", "quic"], "routeOnly": True}
    clients = [{"id": v["vault_check_uuid"], "email": "check"}]
    clients += [{"id": m["uuid"], "email": m["name"]} for m in family(v)]
    xhttp_dest = f"127.0.0.1:{v['vault_ru_xhttp_port']}"
    vision = {
        "tag": VISION_TAG, "listen": "", "port": 443, "protocol": "vless",
        "settings": {"clients": [{**c, "flow": "xtls-rprx-vision"} for c in clients], "decryption": "none"},
        "streamSettings": {
            "network": "tcp", "security": "reality",
            "realitySettings": {
                "target": "127.0.0.1:8443", "xver": 0,
                "serverNames": [v["vault_ru_reality_server_name"]],
                "privateKey": v["vault_ru_reality_private_key"],
                "shortIds": [v["vault_ru_short_id"]],
            },
        },
        "sniffing": sniffing,
    }
    # Authenticated REALITY connections that are not VLESS-over-TCP (i.e. XHTTP's HTTP/2) fall
    # back here. REALITY negotiates no ALPN and XHTTP over it is always h2: one bare rule.
    xhttp = {
        "tag": XHTTP_TAG, "listen": "127.0.0.1", "port": int(v["vault_ru_xhttp_port"]), "protocol": "vless",
        "settings": {"clients": [dict(c) for c in clients], "decryption": "none"},
        "streamSettings": {"network": "xhttp", "security": "none",
                           "xhttpSettings": {"path": v["vault_ru_xhttp_path"], "mode": "auto"}},
        "sniffing": sniffing,
    }
    outbound = to_outbound(profile(v, "link"), tag="nl-exit")
    rules = [
        {"type": "field", "ip": ["geoip:private"], "outboundTag": "blocked"},
        {"type": "field", "protocol": ["bittorrent"], "outboundTag": "blocked"},
        {"type": "field", "domain": ["geosite:category-ru"], "outboundTag": "direct"},
        {"type": "field", "ip": ["geoip:ru"], "outboundTag": "direct"},
        {"type": "field", "inboundTag": [VISION_TAG, XHTTP_TAG], "outboundTag": "nl-exit"},
    ]
    return {"vision": vision, "xhttp": xhttp, "fallback_dest": xhttp_dest, "outbound": outbound,
            "rules": rules, "conn_idle": int(v["xray_conn_idle"])}


def panel_test():
    v = load()
    p = panel_parts(v)
    vision = json.loads(json.dumps(p["vision"]))
    vision["settings"]["fallbacks"] = [{"dest": p["fallback_dest"]}]
    json.dump({
        "dns": RU_DNS,
        "policy": {"levels": {"0": {"connIdle": p["conn_idle"]}}},
        "inbounds": [vision, p["xhttp"]],
        "outbounds": [{"tag": "direct", "protocol": "freedom"}, {"tag": "blocked", "protocol": "blackhole"},
                      p["outbound"]],
        "routing": {"domainStrategy": "IPIfNonMatch", "rules": p["rules"]},
    }, sys.stdout, indent=2)


def ssh(cmd):
    return subprocess.run(["ssh", "-o", "BatchMode=yes", "vpn-srv", cmd],
                          check=True, capture_output=True, text=True).stdout


# Read-only summary of the config 3x-ui generated, without secrets.
VERIFY = r'''
import json
c = json.load(open("/usr/local/x-ui/bin/config.json"))
for i in c["inbounds"]:
    s = i.get("settings") or {}
    cl = s.get("clients") or []
    print("inbound %-14s %s:%s %-5s clients=%d flows=%s fallbacks=%s" % (
        i.get("tag"), i.get("listen") or "*", i.get("port"), (i.get("streamSettings") or {}).get("network", "-"),
        len(cl), sorted({x.get("flow") or "-" for x in cl}),
        [(f.get("name") or "*") + "->" + str(f.get("dest")) for f in s.get("fallbacks") or []]))
for o in c["outbounds"]:
    st = o.get("streamSettings") or {}
    print("outbound %-8s %-9s fp=%s xmux=%s" % (o.get("tag"), o.get("protocol"),
        (st.get("realitySettings") or {}).get("fingerprint", "-"), (st.get("xhttpSettings") or {}).get("xmux", "-")))
print("dns:", (c.get("dns") or {}).get("servers"), "connIdle:",
      ((c.get("policy") or {}).get("levels") or {}).get("0", {}).get("connIdle"))
'''


def panel_apply():
    """Apply panel_parts() through the 3x-ui API over an SSH tunnel. Idempotent."""
    import secrets
    import socket
    import time
    import urllib.request

    v = load()
    p = panel_parts(v)

    backup = ssh("sudo install -d -m 700 /var/backups/x-ui && "
                 "f=/var/backups/x-ui/x-ui.db.$(date +%F-%H%M%S) && sudo cp -p /etc/x-ui/x-ui.db $f && echo $f").strip()
    print("db backup:", backup)
    out = ssh("sudo /usr/local/x-ui/x-ui setting -getApiToken -tokenName vpnctl -tokenScope admin")
    token = next(line.split(": ", 1)[1].strip() for line in out.splitlines() if "apiToken:" in line)

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        lport = s.getsockname()[1]
    tunnel = subprocess.Popen(["ssh", "-N", "-o", "ExitOnForwardFailure=yes",
                               "-L", f"127.0.0.1:{lport}:127.0.0.1:{v['vault_xui_port']}", "vpn-srv"])
    try:
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", lport), timeout=1).close()
                break
            except OSError:
                time.sleep(0.2)
        base = f"http://127.0.0.1:{lport}{v['vault_xui_web_base_path'].rstrip('/')}/panel/api"

        def api(path, body=None, form=None, get=False):
            headers = {"Authorization": f"Bearer {token}"}
            data = None
            if body is not None:
                data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
            elif form is not None:
                data = urllib.parse.urlencode(form).encode()
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            req = urllib.request.Request(base + path, data=data, headers=headers,
                                         method="GET" if get else "POST")
            with urllib.request.urlopen(req, timeout=30) as r:
                res = json.load(r)
            if not res.get("success"):
                sys.exit(f"{path}: {res.get('msg')}")
            return res.get("obj")

        def as_obj(x):
            return json.loads(x) if isinstance(x, str) else (x or {})

        def inbounds():
            return {i["tag"]: i for i in api("/inbounds/list", get=True) or []}

        def emails(inbound):
            return {c["email"] for c in as_obj(inbound["settings"]).get("clients") or []}

        def new_client(c, flow):
            return {**c, "flow": flow, "limitIp": 0, "totalGB": 0, "expiryTime": 0, "enable": True,
                    "tgId": "", "subId": secrets.token_hex(8), "reset": 0}

        have = inbounds()
        # 1. Vision inbound on 443 (created once; clients are reconciled in step 3).
        vis = p["vision"]
        if VISION_TAG not in have:
            stream = json.loads(json.dumps(vis["streamSettings"]))
            stream["realitySettings"].update({"show": False, "spiderX": "/", "settings": {
                "publicKey": v["vault_ru_reality_public_key"], "fingerprint": v["client_fingerprint"],
                "serverName": "", "spiderX": "/"}})
            stream["tcpSettings"] = {"acceptProxyProtocol": False, "header": {"type": "none"}}
            api("/inbounds/add", body={
                "remark": "ru-entry", "enable": True, "listen": "", "port": 443, "protocol": "vless",
                "tag": VISION_TAG,
                "settings": json.dumps({"clients": [new_client(c, "xtls-rprx-vision") for c in vis["settings"]["clients"][:1]],
                                        "decryption": "none", "fallbacks": []}),
                "streamSettings": json.dumps(stream), "sniffing": json.dumps(vis["sniffing"])})
            print(f"inbound {VISION_TAG}: created")
        # 2. XHTTP inbound on loopback, reached only through the Vision inbound's fallback.
        xh = p["xhttp"]
        if XHTTP_TAG not in have:
            api("/inbounds/add", body={
                "remark": "ru-entry-xhttp", "enable": True, "listen": xh["listen"], "port": xh["port"],
                "protocol": "vless", "tag": XHTTP_TAG,
                # Empty: emails must be unique, so people are attached in step 3.
                "settings": json.dumps({"clients": [], "decryption": "none"}),
                "streamSettings": json.dumps(xh["streamSettings"]), "sniffing": json.dumps(xh["sniffing"])})
            print(f"inbound {XHTTP_TAG}: created")
        have = inbounds()
        vid, xid = have[VISION_TAG]["id"], have[XHTTP_TAG]["id"]
        # 3. Every person on both inbounds (one client record, flow per inbound).
        on_vision, on_xhttp = emails(have[VISION_TAG]), emails(have[XHTTP_TAG])
        for c in vis["settings"]["clients"]:
            email = c["email"]
            if email not in on_vision and email not in on_xhttp:
                api("/clients/add", body={"client": new_client(c, "xtls-rprx-vision"), "inboundIds": [vid, xid]})
                print(f"client {email}: created on both inbounds")
            elif email not in on_vision:
                api(f"/clients/{urllib.parse.quote(email)}/attach", body={"inboundIds": [vid]})
                print(f"client {email}: attached to {VISION_TAG}")
            elif email not in on_xhttp:
                api(f"/clients/{urllib.parse.quote(email)}/attach", body={"inboundIds": [xid]})
                print(f"client {email}: attached to {XHTTP_TAG}")
        # 4. The fallback from the Vision inbound to the XHTTP inbound. One bare rule: in Xray a
        # fallback's "name" is the SNI it matches, not a label, so it must stay empty.
        want = [{"childId": xid, "name": "", "alpn": "", "path": "", "dest": p["fallback_dest"],
                 "xver": 0, "sortOrder": 0}]
        cur = api(f"/inbounds/{vid}/fallbacks", get=True) or []
        key = ("childId", "name", "dest", "alpn", "path", "xver")
        if [tuple(f.get(k) or "" if k not in ("childId", "xver") else f.get(k) or 0 for k in key) for f in cur] != \
                [tuple(f[k] for k in key) for f in want]:
            api(f"/inbounds/{vid}/fallbacks", body={"fallbacks": want})
            print(f"fallback {VISION_TAG} -> {p['fallback_dest']}: set")

        # 5. Xray template: link outbound, routing, DNS, idle timeout. The response is
        # {xraySetting, inboundTags, ...}, possibly as a JSON string; only xraySetting is the template.
        cfg = api("/xray/", form={})
        while True:
            if isinstance(cfg, str):
                cfg = json.loads(cfg)
            elif isinstance(cfg, dict) and "xraySetting" in cfg:
                cfg = cfg["xraySetting"]
            else:
                break
        for tag in ("direct", "blocked"):
            if not any(o.get("tag") == tag for o in cfg.get("outbounds", [])):
                sys.exit(f"xray template has no '{tag}' outbound; not saving")
        if not any(i.get("tag") == "api" for i in cfg.get("inbounds", [])):
            sys.exit("xray template has no 'api' inbound; not saving")
        new = json.loads(json.dumps(cfg))
        new["outbounds"] = [o for o in new["outbounds"] if o.get("tag") != "nl-exit"] + [p["outbound"]]
        routing = new.setdefault("routing", {})
        routing["domainStrategy"] = "IPIfNonMatch"
        routing["rules"] = [r for r in routing.get("rules", []) if r.get("outboundTag") == "api"] + p["rules"]
        new["dns"] = RU_DNS
        new.setdefault("policy", {}).setdefault("levels", {}).setdefault("0", {})["connIdle"] = p["conn_idle"]
        if new != cfg:
            api("/xray/update", form={"xraySetting": json.dumps(new)})
            print("xray template: updated")
        else:
            print("xray template: unchanged")
        # A full restart: hot-applied outbounds keep pooled connections built with old settings.
        api("/server/restartXrayService", form={})
        print("xray: restarted")
    finally:
        tunnel.terminate()
    time.sleep(3)
    print(ssh("sudo python3 -c " + shlex.quote(VERIFY)), end="")


if __name__ == "__main__":
    cmd, args = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else ("", [])
    if cmd == "links":
        links(args)
    elif cmd == "sub" and len(args) == 1:
        sub(args[0])
    elif cmd == "client" and len(args) == 1:
        client(args[0])
    elif cmd == "panel-test" and not args:
        panel_test()
    elif cmd == "panel-apply" and not args:
        panel_apply()
    else:
        sys.exit(__doc__)
