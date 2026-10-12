# CLAUDE.md — Family VPN (RU → NL cascade on Xray)

## Language

**Always respond to the user in Russian.** Code, comments, commit messages and file contents stay in English unless the user asks otherwise. Family-facing docs (`docs/clients.md`) are written in Russian.

## Goal

A private VPN for the family (5–10 devices), resilient to Russian DPI (TSPU) blocking.
Priorities, in order: **survivability → security → ease of maintenance → speed**.
The setup is fully reproducible: any server can be wiped and rebuilt with one command.

## Architecture

```
Client ──VLESS+XHTTP+Reality (primary)───┐
Client ──VLESS+Reality TCP vision (2nd)──┴▶ ru-entry:443 ──VLESS+XHTTP+Reality──▶ nl-exit ──▶ internet
                                               │
                                               └── geoip:ru / geosite:category-ru ──▶ direct
Client ──VLESS+XHTTP+Reality (3rd, "Резерв")──────────────────────────────────────▶ nl-exit:443
```

Each person gets one subscription with these three profiles, in this order. On ru-entry both
profiles share 443: the Vision+REALITY inbound hands authenticated non-VLESS traffic (XHTTP's h2)
to an XHTTP inbound on 127.0.0.1 through a VLESS fallback.

### Why it looks like this (TSPU, 2026)

Measured from a Russian home ISP on 2026-10-12, matching public reports:
- **Burst freeze:** more than ~3 parallel new TLS handshakes to one datacenter IP (Russian ones
  included) with a "suspicious" uTLS fingerprint (chrome, safari, iOS) freeze all TLS to that IP
  for ~120 s. TCP+Vision opens one connection per browser request, so a page load triggers it.
  Countermeasures, both kept: XHTTP with a small `xmux` pool (multiplexes everything over
  1–2 connections), and fingerprint `firefox`. Measured on ru-entry with the *flagged* chrome
  fingerprint, 5 rounds × 6 parallel requests: XHTTP 30/30 on a steady 2 connections, Vision
  7/30 with connections piling up to 23. The pool is the real fix; the fingerprint covers app
  start-up (Xray #6797) and the Vision profile.
- **REALITY in Xray ≥ 26.9.8 requires X25519MLKEM768** in the ClientHello: only uTLS chrome,
  firefox and safari work (edge/android/ios/360/qq/randomized are rejected). sing-box clients
  (Hiddify, Karing) cannot connect.
- **SNI blocking from Russian datacenters:** a Reality dest must open from ru-entry itself
  (`www.surf.nl` did not, which broke the link).
- **16–20 KB freeze** towards foreign hosting on some networks: the direct-to-NL profile is the
  last resort, not the primary.
- **Mobile whitelist mode** (about half of mobile sessions in Moscow/SPb in mid-2026): VDSina IPs
  are not whitelisted, so nothing here works on mobile data while it is on. Use Wi-Fi. No
  reliable fix exists (see Open questions).
- **IP bans** of Reality servers (self-steal included) happen in waves; a banned IP is
  unreachable from Russian datacenters too.

| Host | Location | OS | Role | Software |
|---|---|---|---|---|
| `ru-entry` | VDSina, Russia — **shared with the botato relay** (WSL alias `vpn-srv`) | Debian 13 | Client entry point, split routing, users and subscriptions | 3x-ui (bundles Xray) |
| `nl-exit` | VDSina, Netherlands (WSL alias `nl-exit`) | Debian 13 | Internet exit, fallback entry | Bare Xray (systemd), no panel |

ru-entry is the botato relay VPS (`H:\Desktop\botato`). botato's Ansible owns users, sshd, nftables
(table `inet botato`) and fwknop there; this repo installs only 3x-ui, the geodata timer and BBR.
Port 443 is shared by **Reality self-steal**: Xray listens on 443, its dest is botato's Caddy on
`127.0.0.1:8443`, SNI = botato's relay domain (same IP). Every non-VPN TLS connection — botato
clients, ACME TLS-ALPN — goes through to Caddy unchanged. botato switch: `caddy_behind_xray`.

### Ports

| Host | Port | Purpose | Access |
|---|---|---|---|
| ru-entry | 443/tcp | VLESS + Reality (TCP, `xtls-rprx-vision`); XHTTP → fallback; non-VPN TLS → Caddy | public |
| ru-entry | 127.0.0.1:`XHTTP_PORT` | VLESS + XHTTP inbound (security none), reached only via the 443 fallback | local |
| ru-entry | 127.0.0.1:8443 | botato Caddy (Reality dest; also serves subscriptions at `/s/<token>`) | local |
| ru-entry | 22/tcp | SSH, keys only | **after fwknop knock only** (botato) |
| ru-entry | 62201/udp | fwknopd (botato) | public, never replies |
| ru-entry | 3478, 5349, coturn range | botato TURN | public (botato) |
| ru-entry | 127.0.0.1:`PANEL_PORT` | 3x-ui web panel | **SSH tunnel only** |
| ru-entry | (`SUB_PORT`) | 3x-ui's own subscription server | **disabled** (see "Subscriptions") |
| nl-exit | `LINK_PORT`/tcp | VLESS + XHTTP + Reality for the cascade link | **ru-entry IP only** |
| nl-exit | 443/tcp | VLESS + XHTTP + Reality, fallback entry | public |
| nl-exit | 22/tcp | SSH, keys only | **after fwknop knock only** |
| nl-exit | 62201/udp | fwknopd | public, never replies |

## Repository layout

```
.
├── CLAUDE.md
├── ansible.cfg                   # used via ./ansible.sh (Ansible ignores it in world-writable /mnt/h)
├── ansible.sh                    # WSL wrapper: ./ansible.sh playbooks/x.yml | ./ansible.sh -m ping all
├── inventory/
│   ├── hosts.yml                 # ru-entry, nl-exit (IPs via vault variables)
│   ├── host_vars/                # fwknop stanza, Reality dests, firewall ports per host
│   └── group_vars/
│       ├── all/vars.yml          # pinned versions + checksums, common settings
│       └── all/vault.yml         # ansible-vault: UUIDs, Reality keys, shortIds, IPs, ports, panel creds
├── playbooks/
│   ├── site.yml                  # everything
│   ├── bootstrap.yml             # fresh nl-exit as root: users, sshd hardening, root locked
│   ├── base.yml                  # Debian hardening + fwknop + firewall (nl-exit only)
│   ├── ru-entry.yml              # bbr + x-ui + vpn-sub
│   ├── nl-exit.yml               # base.yml + xray
│   └── tasks/knock.yml           # fwknop SPA from the control machine
├── roles/
│   ├── base/                     # packages, no swap, volatile journald, unattended-upgrades, timesyncd
│   ├── bbr/                      # fq + bbr sysctl
│   ├── fwknop/                   # fwknopd; keys from ~/.fwknoprc on the control machine
│   ├── firewall/                 # nftables table inet vpn, applied with a dead man's switch
│   ├── xray/                     # pinned Xray install, systemd unit, validated config.json
│   ├── x-ui/                     # pinned 3x-ui install, panel on 127.0.0.1, its sub server off, geodata timer
│   └── vpn-sub/                  # /opt/vpn-sub/<token>: static subscriptions rendered by vpnctl.py
├── templates/
│   └── nl-exit.config.json.j2
├── docs/
│   ├── ru-entry-panel.md         # what panel-apply configures in 3x-ui, and how to do it by hand
│   └── clients.md                # client instructions for family members (in Russian)
└── scripts/
    ├── check.sh                  # smoke test for both nodes
    ├── vpnctl.py                 # profiles → links/subscriptions, check-client configs, panel-apply (3x-ui API)
    └── reality-dest-scan.md      # how to pick dest/SNI with RealiTLScanner
```

Tooling: **Ansible**, run only from WSL (Debian). Playbooks must be idempotent — a second run changes nothing.
On a fresh nl-exit: `bootstrap.yml` (as root) → `nl-exit.yml --skip-tags firewall` → `base.yml --tags firewall`.

## Secrets

- All secrets (client UUIDs, Reality private keys, shortIds, server IPs, ports, panel credentials) live **only** in `vault.yml` under ansible-vault.
- Vault password: WSL `~/.config/vpn/vault_pass` (outside the repo: files on /mnt/h look executable, and Ansible would run it as a script). Losing it means regenerating every secret.
- fwknop SPA keys: WSL `~/.fwknoprc`, stanzas `[vpn-srv]` (botato) and `[nl-exit]`; `~/.ssh/config` knocks by itself for `ssh vpn-srv` / `ssh nl-exit`.
- Never committed: private keys, `x-ui.db`, subscription links, `vless://` client links, plaintext IPs.
- `vault_family` entries are `{name, uuid, sub}`: `sub` (`openssl rand -hex 16`) is the person's subscription token, i.e. their link `https://<relay domain>/s/<sub>`. Rotating it means sending a new link.
- `.gitignore` must cover `*.db`, `*.key`, `.vault_pass`, `out/`.
- Reality keys come from `xray x25519`, UUIDs from `xray uuid`, shortIds from `openssl rand -hex 8`. Generate once by hand and store in the vault; do not automate generation in playbooks.

## Rules for Claude

### Access safety (most important)

- **Never lose SSH access.** Any change to `sshd_config` or the firewall goes like this:
  1. validate syntax: `sshd -t` or `nft -c -f <file>`;
  2. apply with automatic rollback: the `firewall` role arms `systemd-run --on-active=180 nft delete table inet vpn`, applies, reconnects through a fresh knock and only then disarms;
  3. keep the current SSH session open until a second session connects successfully.
- SSH is reachable only after an fwknop knock (`ssh nl-exit` / `ssh vpn-srv` knock by themselves; playbooks knock in `pre_tasks`). Fallback access: VNC console in the VDSina panel.
- **ru-entry is botato's production server.** Its sshd, users, nftables and fwknop are changed only through botato's Ansible, and only with the user's explicit confirmation. Never apply this repo's `base`/`fwknop`/`firewall` roles to it. Anything that touches 443 there (Xray inbound, `caddy_behind_xray`) also affects the botato relay — confirm first.
- Never enable `PasswordAuthentication`, never expose the 3x-ui panel publicly, never open ports not listed in the table above.
- Ask the user before any destructive action on the servers (reinstall, `rm`, resetting the 3x-ui database, rotating Reality keys). Rotating Reality keys breaks every family member's subscription.

### Xray

- Xray and 3x-ui versions are **pinned** in `group_vars/all/vars.yml`. Upgrade only on explicit request, after checking the changelog for breaking changes.
- Every Xray config is validated before restart: `xray run -test -c <config.json>`.
- Reality:
  - nl-exit: pick `dest`/`serverNames` with RealiTLScanner over the server's own subnet: TLS 1.3, H2, close by IP, a European site, not behind a CDN (`scripts/reality-dest-scan.md`). Current: `www.transip.nl` (443), `www.utwente.nl` (link). The dest must also open from ru-entry: TSPU drops some SNIs (www.surf.nl was blocked that way).
  - ru-entry: self-steal — dest `127.0.0.1:8443` (botato Caddy), SNI = botato relay domain (`vault_ru_reality_server_name`).
  - `fingerprint`: `client_fingerprint` in `vars.yml`, currently `firefox` (see "Why it looks like this"). Only chrome/firefox/safari are accepted by REALITY in Xray ≥ 26.9.8; never `randomized`.
  - Never reuse the same dest on both nodes.
- Client profiles on ru-entry, both on 443: **primary** VLESS + XHTTP + Reality (no `flow`) via the fallback to the loopback XHTTP inbound; **second** VLESS + Reality, TCP, `flow: xtls-rprx-vision`.
- ru-entry → nl-exit link: VLESS + XHTTP + Reality (no `flow`; vision is not used with XHTTP).
- Every XHTTP client side (family profiles and the link) carries `xhttp_xmux` from `vars.yml` (small `maxConnections`, explicit rotation). Setting any xmux field disables the rest of Xray's defaults.
- Cores ≥ 26.9.8 built with Go 1.27 may still open a burst of connections before the first one is up (Xray #6797): the fingerprint is the second layer for that case.
- `connIdle` = `xray_conn_idle` (1 h) on both nodes, for long idle sessions.
- Do not use VLESS without TLS/Reality, Shadowsocks, WireGuard, OpenVPN or IPsec — they are detected from inside Russia.
- Before blaming the protocol, measure: parallel requests (like a browser), with timings, from a Russian network without other VPNs/proxies in the path; compare fingerprints and test the dest from ru-entry.

### Routing on ru-entry

- `geoip:private` → block; `bittorrent` → block (on nl-exit too: abuse complaints).
- `geoip:ru`, `geosite:category-ru` → direct.
- Everything else from `inbound-443` / `inbound-xhttp` → `nl-exit` outbound.
- Domain strategy `IPIfNonMatch`, so ru-entry resolves domains; Xray uses its own DNS there (Yandex 77.88.8.8/77.88.8.1, then the system resolver): the host's 8.8.8.8 is blocked in Russia from time to time.
- geoip/geosite are runetfreedom builds, refreshed weekly by `vpn-geodata-update.timer` (sha256-checked).

### 3x-ui

- 3x-ui stores its config in SQLite (`/etc/x-ui/x-ui.db`). Ansible installs it, binds the panel to localhost and turns its subscription server off; inbounds, clients, the fallback, the link outbound, routing, DNS and `connIdle` are applied from the vault by `scripts/vpnctl.py panel-apply` (3x-ui API over an SSH tunnel, idempotent, backs up `x-ui.db` first, prints a secret-free summary of the generated config). `docs/ru-entry-panel.md` describes the result and the manual way.
- Back up `x-ui.db` before any panel change (on the server, never into git). panel-apply does it itself.
- One client (own UUID) per family member with a clear email label, attached to both ru-entry inbounds (3x-ui keeps the flow per inbound); no traffic limits unless requested. The same UUID is used on all nodes. To add a person: append `{name, uuid, sub}` to `vault_family`, then `panel-apply`, `./ansible.sh playbooks/nl-exit.yml --tags xray`, `./ansible.sh playbooks/ru-entry.yml --tags sub`, `scripts/vpnctl.py links <name>`.
- Service client `check` (`vault_check_uuid`) exists on both nodes for `scripts/check.sh`.

### Subscriptions

- Decided (2026-10-12): one subscription per person with three profiles (Основной / Запасной / Резерв (NL)), as static files served by botato's Caddy on 443 at `https://<relay domain>/s/<token>`.
  - `roles/vpn-sub` renders them from the vault (`vpnctl.py sub`) into `/opt/vpn-sub` on ru-entry; botato mounts that directory read-only into Caddy (`handle_path /s/*`, no listing, unknown token → 404).
  - Changing a transport, fingerprint or dest = change the vault/vars, re-run panel-apply/playbooks: client apps pick it up on their next subscription refresh. That is the main defence against new blocking rules.
- Never expose 3x-ui's own subscription port.

## Debian hardening (`base` role)

nl-exit only; ru-entry gets the same from botato.

- Minimal image, no extra packages. No swap, journald in RAM only.
- `bootstrap.yml`: `vegameta` (key + sudo password from the vault), `deploy` (key `~/.ssh/deploy_ed25519`, NOPASSWD sudo, used by Ansible), `PermitRootLogin no`, `PasswordAuthentication no`, root password locked.
- nftables (`firewall` role, table `inet vpn`): default drop on input; allow established/related, loopback, the ports from the table.
- SSH on 22 behind fwknop SPA (`fwknop` role) instead of fail2ban: the port is closed to anyone who has not knocked.
- unattended-upgrades for security updates only.
- BBR: `net.core.default_qdisc=fq`, `net.ipv4.tcp_congestion_control=bbr`.
- Check AES-NI: `grep -m1 -o aes /proc/cpuinfo`.
- Time sync (systemd-timesyncd): Reality is sensitive to clock skew.

## Verification (`scripts/check.sh`)

After every change (from WSL: `scripts/check.sh`, uses the `check` client and a local Xray in `~/.local/share/vpn-tools/xray-dist`):

1. `systemctl is-active xray` (nl-exit) / `systemctl is-active x-ui` (ru-entry).
2. `ss -tlnp` — only the ports from the table are open; panel and XHTTP inbound on 127.0.0.1.
3. `xray run -test -c ...` — config is valid.
4. Each profile (xhttp, vision via ru-entry; fallback via nl-exit): foreign site sees the nl-exit IP; through ru-entry a Russian site sees the ru-entry IP.
5. Each profile survives browser-like load: 3 rounds × 6 parallel requests.
6. The subscription answers with three profiles; an unknown token is a 404; `LINK_PORT` on nl-exit is closed to an unrelated IP.
7. Test on several networks by hand: home ISP and at least two mobile carriers.

## Clients (for `docs/clients.md`)

Every app must bundle a current Xray core (REALITY needs X25519MLKEM768 + firefox fingerprint) and read `extra` (xmux) from links:
- iOS: **Happ** (core 26.9.x). It is gone from the Russian App Store: install with a non-Russian Apple ID signed in only under App Store / Media & Purchases. INCY is in the Russian store but closed source.
- Android: **Happ** (Google Play or APK); v2RayTun / v2rayNG work too (older core, firefox verified with 26.6.27).
- Windows / macOS: **Happ** desktop, or **v2rayN** ≥ 7.24.9.
- **Not** Hiddify, Karing or other sing-box apps: they cannot connect to this REALITY.
- Family instructions in plain Russian, no jargon: "install the app → add the subscription link → turn it on".

## Open questions

- Mobile whitelist mode: no reliable fix. Options researched (2026-10): an extra entry on a Russian cloud IP that happens to be whitelisted (a lottery, IPs get removed), XHTTP through Yandex Cloud CDN (works for some, accounts get banned, resources live ~10 days). Not done.
- nl-exit is both the public fallback entry and the exit: a ban of its IP kills the cascade too. A separate IP or VPS for the fallback was considered and declined for now (2026-10-12).
- ru-entry shares its IP with the botato relay: a ban of that IP takes both down (kept by decision, 2026-10-12).
- Whether to add a third exit (different provider/country) in case VDSina subnets get banned.
- Monitoring: is `check.sh` on cron with Telegram alerts enough, or is something more needed.
