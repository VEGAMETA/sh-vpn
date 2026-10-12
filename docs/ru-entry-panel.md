# ru-entry: 3x-ui configuration

3x-ui keeps inbounds, clients, outbounds and routing in `/etc/x-ui/x-ui.db`. Ansible
(`playbooks/ru-entry.yml`) installs the panel, binds it to 127.0.0.1, sets its credentials and
turns its own subscription server off. Everything else is applied from the vault:

```sh
scripts/vpnctl.py panel-apply      # from WSL in the repo root; idempotent
```

It backs up `x-ui.db` to `/var/backups/x-ui/`, mints an API token (`x-ui setting -getApiToken`),
talks to the panel through an SSH tunnel, restarts Xray only if something changed (a restart drops
every live session and blinks the botato relay) and prints a secret-free summary of the generated
`/usr/local/x-ui/bin/config.json`. Expected summary:

```
inbound api            127.0.0.1:62789 -     clients=0 flows=['-'] fallbacks=[]
inbound inbound-443    *:443 tcp   clients=N flows=['xtls-rprx-vision'] fallbacks=['127.0.0.1:<XHTTP_PORT>']
inbound inbound-xhttp  127.0.0.1:<XHTTP_PORT> xhttp clients=N flows=['-'] fallbacks=[]
outbound direct ... blocked ... nl-exit vless fp=firefox xmux={...}
dns: ['77.88.8.8', '77.88.8.1', 'localhost'] connIdle: 3600
```

`flows` on `inbound-xhttp` must be `['-']`: Vision does not work over XHTTP.

## What it configures

ru-entry shares the VPS with the botato relay. 443 uses **Reality self-steal**: Xray owns 443,
and every TLS connection that is not ours goes to botato's Caddy on `127.0.0.1:8443`, so the SNI
is botato's own domain, resolving to this very IP (`caddy_behind_xray: true` in botato).

1. **`inbound-443`**: VLESS, TCP, Reality.
   - dest `127.0.0.1:8443`, serverNames = the relay domain, the vault's private key and shortId.
   - Clients have flow `xtls-rprx-vision`.
   - Sniffing http/tls/quic, route only.
   - Fallback: one bare rule (no alpn, no path) to `127.0.0.1:<XHTTP_PORT>`. REALITY negotiates
     no ALPN and XHTTP over it is always h2, so alpn/path rules never match.
2. **`inbound-xhttp`**: VLESS, XHTTP (`path` from the vault, mode auto), security none, on
   127.0.0.1. Only REALITY-authenticated traffic reaches it.
3. **Clients**: `check` plus everyone in `vault_family`, as one client record each, attached to
   both inbounds. The flow is kept per inbound.
4. **Outbound `nl-exit`**: VLESS + XHTTP + Reality to `nl-exit:LINK_PORT`, without `flow`.
   - SNI is `link_reality_target`.
   - Fingerprint is `client_fingerprint`; xmux is `xhttp_xmux`.
   - The default `direct` and `blocked` outbounds stay.
5. **Routing**, `IPIfNonMatch`, after the default `api` rule:
   1. `geoip:private` → `blocked`;
   2. protocol `bittorrent` → `blocked`;
   3. `geosite:category-ru` → `direct`;
   4. `geoip:ru` → `direct`;
   5. inbounds `inbound-443`, `inbound-xhttp` → `nl-exit`.
6. **DNS** for Xray: `77.88.8.8`, `77.88.8.1`, then `localhost`; IPv4 only.
7. **Policy**: `connIdle` = `xray_conn_idle`.

`scripts/vpnctl.py panel-test` prints the same pieces as one Xray config. Check it with
`xray run -test -c` before touching the server.

## Access by hand

```sh
ssh -N -L 2053:127.0.0.1:<PANEL_PORT> vpn-srv   # PANEL_PORT etc.: ansible-vault view inventory/group_vars/all/vault.yml
```

Then open `http://127.0.0.1:2053<webBasePath>`. Before **every** manual change:

```sh
ssh vpn-srv 'sudo install -d -m 700 /var/backups/x-ui && sudo cp -p /etc/x-ui/x-ui.db /var/backups/x-ui/x-ui.db.$(date +%F-%H%M)'
```

Manual changes are overwritten by the next panel-apply where they conflict. Put them in
`vpnctl.py` instead. Never press "Get New Cert" or regenerate keys on an inbound: new keys
break every subscription.

## First cutover (done 2026-10-12)

Caddy and Xray cannot both hold 443:

1. Run panel-apply. Xray fails to bind 443 for now; that is expected.
2. Deploy botato with `caddy_behind_xray: true`. Caddy moves to `127.0.0.1:8443` and nftables
   opens 443.
3. Restart x-ui, run `scripts/check.sh`, check the botato relay.

Rollback: `caddy_behind_xray: false` in botato and deploy; `sudo systemctl stop x-ui`.
