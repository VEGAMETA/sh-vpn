# Picking a Reality dest / SNI

Requirements: TLS 1.3, h2, X25519, a real site (not another Reality server), not behind a CDN
(Cloudflare, CloudFront, Akamai), close to the server by network. A European site for nl-exit,
a Russian one for ru-entry. Never the same dest on both nodes.

ru-entry is the exception: it uses self-steal (dest = botato's Caddy on 127.0.0.1:8443,
SNI = botato's own domain). Nothing to scan there.

## 1. Scan the server's /24 with RealiTLScanner (from WSL)

```sh
curl -sLo scanner https://github.com/XTLS/RealiTLScanner/releases/download/v0.2.3/RealiTLScanner-linux-amd64
chmod +x scanner
./scanner -addr <server-ip>/24 -port 443 -thread 8 -timeout 5 -out scan.csv
```

Many neighbours in VDSina subnets are themselves Reality servers: they show certificates of
github.com, yahoo.com, vk.com, cloudflare.com, yandex.* that do not belong to that IP.
Skip those. The scan gives ideas, not answers.

## 2. Check candidates from the server itself

```sh
for h in www.surf.nl www.transip.nl www.utwente.nl; do
  echo "$h $(getent ahostsv4 $h | awk 'NR==1{print $1}') \
    $(curl -so /dev/null --tlsv1.3 --http2 -w '%{http_version} %{time_connect}s' https://$h/) \
    $(curl -sI https://$h/ | grep -i -E '^(server|via|cf-ray):' | tr -d '\r' | tr '\n' ' ')"
done
```

Want: HTTP version `2`, connect time of a few ms, `server: nginx`/own hosting, no `cf-ray`,
no `via: ... cloudfront`.

## 3. Check that Russia can reach it (mandatory)

TSPU drops some SNIs outright. With such a dest the Reality handshake itself gets dropped, and
the link hangs on every other connection. From ru-entry (and ideally a Russian home ISP):

```sh
for h in www.utwente.nl www.transip.nl; do
  ok=0; for i in 1 2 3 4 5 6; do [ "$(curl -s -o /dev/null -m 5 -w '%{http_code}' https://$h/)" != 000 ] && ok=$((ok+1)); done
  echo "$h $ok/6"
done
```

Only 6/6 qualifies. After switching, check the handshake to the node itself with that SNI:
`curl -sk -o /dev/null -w '%{http_code}' --resolve <sni>:<port>:<node-ip> https://<sni>:<port>/` several times.

## Current choice (2026-10-12)

| Node / inbound | dest | Why |
|---|---|---|
| nl-exit 443 (fallback entry) | `www.transip.nl` | Dutch hoster, own DC, 2.4 ms, nginx, TLS 1.3 + h2 |
| nl-exit LINK_PORT (cascade) | `www.utwente.nl` | University of Twente, own hosting, 10 ms, nginx, TLS 1.3 + h2, 6/6 from ru-entry |

`www.surf.nl` was the first link dest and failed: TSPU blocks it by SNI (0/6 from ru-entry),
which left the cascade hanging on most connections.
| ru-entry 443 | self-steal: botato Caddy | own domain on the same IP |

Changing a dest: edit `inventory/host_vars/nl-exit.yml`, run `./ansible.sh playbooks/nl-exit.yml --tags xray`,
then re-issue links (`scripts/vpnctl.py links`) for the fallback, or update the panel outbound for the link.
