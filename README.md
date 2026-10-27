# sh-vpn

A private family VPN (5–10 devices) that keeps working under Russian DPI (TSPU) blocking: a two-node Xray cascade, Russia → Netherlands, fully described in Ansible. Any server can be wiped and rebuilt with one command.

Priorities, in order: **survivability → security → ease of maintenance → speed**.

## Architecture

```
Client ──VLESS+XHTTP+Reality (primary)───┐
Client ──VLESS+Reality TCP vision (2nd)──┴▶ ru-entry:443 ──VLESS+XHTTP+Reality──▶ nl-exit ──▶ internet
                                               │
                                               └── Russian IPs and domains ──▶ direct
Client ──VLESS+XHTTP+Reality (3rd, reserve)───────────────────────────────────────▶ nl-exit:443
```

- **ru-entry** — client entry point and split routing: Russian sites go direct, everything else goes to the exit node. Runs 3x-ui (bundled Xray), configured from code through its API, with the panel bound to localhost.
- **nl-exit** — internet exit and the last-resort direct entry. Bare Xray under systemd, no panel.
- Each person gets one subscription with three profiles (primary, second, reserve), served as static files. Changing a transport, fingerprint or Reality destination is a variable change plus a playbook run; client apps pick it up on their next subscription refresh.

## Why it looks like this

Measured from a Russian home ISP in 2026:

- More than ~3 parallel new TLS handshakes to one datacenter IP with a flagged fingerprint freeze all TLS to that IP for about two minutes. TCP+Vision opens a connection per browser request and trips it on every page load.
- The fix is XHTTP with a small, explicitly rotated `xmux` pool: everything is multiplexed over 1–2 connections. With the flagged fingerprint, 5 rounds × 6 parallel requests: **XHTTP 30/30** on a steady 2 connections, **Vision 7/30** with connections piling up to 23.
- The uTLS fingerprint is `firefox` as a second layer, for client start-up bursts.
- Reality destinations are picked with RealiTLScanner on the server's own subnet and must open from the entry node itself: some SNIs are dropped from Russian datacenters.
- Not used: plain VLESS without Reality, Shadowsocks, WireGuard, OpenVPN, IPsec — all detected.

## Security

- Admin SSH is reachable only after an fwknop single-packet knock; keys only, root locked.
- nftables is applied with a dead man's switch: a timer rolls the ruleset back unless a fresh connection succeeds after the change.
- Secrets (UUIDs, Reality keys, short IDs, addresses, panel credentials) live in `ansible-vault`; the vault password and rendered client links are gitignored.
- Xray and 3x-ui versions are pinned with checksums; every config is checked with `xray run -test` before a restart.
- Minimal footprint on the servers: no swap, volatile journald, unattended security upgrades; BitTorrent and private ranges are blocked.

## Layout

```
inventory/       hosts, per-host vars, pinned versions (vars.yml), secrets (vault.yml, encrypted)
playbooks/       site.yml (everything), bootstrap, base, ru-entry, nl-exit, tasks/knock.yml
roles/           base, bbr, firewall, fwknop, x-ui, xray, vpn-sub
templates/       nl-exit Xray config
scripts/         vpnctl.py (profiles → links and subscriptions, 3x-ui panel-apply), check.sh (smoke test)
docs/            ru-entry-panel.md, clients.md (instructions for family members, in Russian)
```

## Usage

Run from WSL; `ansible.sh` is a wrapper that applies `ansible.cfg` from a Windows-mounted folder.

```sh
./ansible.sh playbooks/site.yml          # everything
./ansible.sh -m ping all

# fresh nl-exit
./ansible.sh playbooks/bootstrap.yml -l nl-exit -e ansible_user=root   # users, sshd hardening, root locked
./ansible.sh playbooks/nl-exit.yml --skip-tags firewall
./ansible.sh playbooks/base.yml --tags firewall

scripts/vpnctl.py panel-apply            # 3x-ui inbounds, clients, routing from the vault (backs up the panel DB first)
scripts/vpnctl.py links <name>           # one person's links
scripts/check.sh                         # both nodes end to end
```

Adding a person: append them to `vault_family`, then `panel-apply`, `nl-exit.yml --tags xray`, `ru-entry.yml --tags sub`.
