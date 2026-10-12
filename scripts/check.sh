#!/usr/bin/env bash
# Smoke test for both nodes. Run from WSL in the repo root: scripts/check.sh
# Needs: ssh aliases nl-exit / vpn-srv (they knock by themselves), nmap, curl,
# a local xray binary (XRAY, default ~/.local/share/vpn-tools/xray-dist/xray).
# Run it from a Russian network without other VPNs/proxies in the way: it measures what TSPU does.
set -uo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
XRAY=${XRAY:-$HOME/.local/share/vpn-tools/xray-dist/xray}
VAULT_PASS=~/.config/vpn/vault_pass
fail=0
ok()  { printf '  \e[32mok\e[0m   %s\n' "$*"; }
bad() { printf '  \e[31mFAIL\e[0m %s\n' "$*"; fail=1; }
# One SSH connection per host for the whole run: a fresh knock + connect per call is flaky.
ssh() { command ssh -o BatchMode=yes -o ControlMaster=auto -o ControlPath="$HOME/.ssh/cm-check-%h" -o ControlPersist=60 "$@"; }
vault_plain=$(ansible-vault view --vault-password-file "$VAULT_PASS" inventory/group_vars/all/vault.yml 2>/dev/null)
vault() { awk -v k="$1:" '$1 == k {print $2}' <<<"$vault_plain" | tr -d '"'; }

RU_IP=$(vault vault_ru_ip); NL_IP=$(vault vault_nl_ip); LINK_PORT=$(vault vault_link_port)
PANEL_PORT=$(vault vault_xui_port); XHTTP_PORT=$(vault vault_ru_xhttp_port); DOMAIN=$(vault vault_ru_reality_server_name)
SUB_TOKEN=$(grep -m1 -o 'sub: "[0-9a-f]*"' <<<"$vault_plain" | cut -d'"' -f2)
unset vault_plain

echo "1. Services"
[[ $(ssh nl-exit systemctl is-active xray) == active ]] && ok "nl-exit: xray active" || bad "nl-exit: xray not active"
[[ $(ssh vpn-srv systemctl is-active x-ui) == active ]] && ok "ru-entry: x-ui active" || bad "ru-entry: x-ui not active"

echo "2. Listening ports"
nl_ports=$(ssh nl-exit "sudo ss -Htln" | awk '{print $4}' | sort -u | tr '\n' ' ')
for p in $nl_ports; do
  case "$p" in
    *:443|*:"$LINK_PORT"|*:22|127.0.0.1:*|\[::1\]:*) ;;
    *) bad "nl-exit: unexpected listener $p" ;;
  esac
done
ok "nl-exit listeners: $nl_ports"
ru_ss=$(ssh vpn-srv "sudo ss -Htlnp")
grep -q "127.0.0.1:$PANEL_PORT " <<<"$ru_ss" && ok "ru-entry: panel on 127.0.0.1:$PANEL_PORT" || bad "ru-entry: panel is not on 127.0.0.1:$PANEL_PORT"
grep -E "x-ui|xray" <<<"$ru_ss" | awk '{print $4}' | grep -v -E '^(127\.0\.0\.1|\[::1\]):' | grep -v -E ':443$' \
  && bad "ru-entry: x-ui/xray listens publicly beyond 443" || ok "ru-entry: only 443 public (panel, API, XHTTP on loopback)"
grep -q "127.0.0.1:$XHTTP_PORT " <<<"$ru_ss" && ok "ru-entry: XHTTP inbound on 127.0.0.1" || bad "ru-entry: XHTTP inbound not listening"
grep -q '127.0.0.1:8443 ' <<<"$ru_ss" && ok "ru-entry: Caddy on 127.0.0.1:8443" || bad "ru-entry: Caddy not on 127.0.0.1:8443"
grep -E '[*0-9.]+:443 ' <<<"$ru_ss" | grep -q xray && ok "ru-entry: xray on 443" || bad "ru-entry: xray not on 443"

echo "3. Config validity"
ssh nl-exit "sudo env XRAY_LOCATION_ASSET=/usr/local/share/xray /usr/local/bin/xray run -test -c /usr/local/etc/xray/config.json" >/dev/null 2>&1 \
  && ok "nl-exit: config valid" || bad "nl-exit: config invalid"

echo "4-5. Through each profile (user 'check')"
pid=""
start() { cfg=$(mktemp --suffix=.json); chmod 600 "$cfg"; scripts/vpnctl.py client "$1" > "$cfg"
          "$XRAY" run -c "$cfg" >/dev/null 2>&1 & pid=$!; sleep 2; }
stop()  { kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null; rm -f "$cfg"; }
get()   { curl -s -m 10 --socks5-hostname 127.0.0.1:10808 "$1" | tr -d '"\n'; }
# Like a browser opening a page: 6 requests at once, 3 times.
burst() { local n=0 r u
  for r in 1 2 3; do
    n=$((n + $(for u in https://ya.ru https://icanhazip.com https://www.youtube.com https://ifconfig.me/ip https://vk.com https://api.ipify.org; do
                 curl -s -o /dev/null -m 10 --socks5-hostname 127.0.0.1:10808 -w '%{http_code}\n' "$u" &
               done | grep -vc '^000$')))
    sleep 2
  done; echo "$n"; }
for prof in xhttp vision fallback; do
  start "$prof"
  ip=$(get https://api.ipify.org)
  [[ $ip == "$NL_IP" ]] && ok "$prof: foreign site sees nl-exit" || bad "$prof: foreign site sees '$ip', want $NL_IP"
  if [[ $prof != fallback ]]; then
    ip=$(get https://ipv4-internet.yandex.net/api/v0/ip)
    [[ $ip == "$RU_IP" ]] && ok "$prof: Russian site sees ru-entry" || bad "$prof: Russian site sees '$ip', want $RU_IP"
  fi
  n=$(burst)
  [[ $n -ge 17 ]] && ok "$prof: parallel load $n/18" || bad "$prof: parallel load $n/18 (TSPU freeze?)"
  stop
done

echo "6. Subscription and exposure"
code=$(curl -s -o /tmp/vpn-sub.$$ -m 10 -w '%{http_code}' "https://$DOMAIN/s/$SUB_TOKEN")
links=$(base64 -d /tmp/vpn-sub.$$ 2>/dev/null | grep -c '^vless://'); rm -f /tmp/vpn-sub.$$
[[ $code == 200 && $links == 3 ]] && ok "subscription: 200, $links profiles" || bad "subscription: HTTP $code, $links profiles"
code=$(curl -s -o /dev/null -m 10 -w '%{http_code}' "https://$DOMAIN/s/0000")
[[ $code == 404 ]] && ok "subscription: unknown token is 404" || bad "subscription: unknown token gives $code"
st=$(nmap -Pn -p "$LINK_PORT" "$NL_IP" 2>/dev/null | awk '/^[0-9]+\/tcp/{print $2}')
[[ $st != open ]] && ok "nl-exit: LINK_PORT closed to the world" || bad "nl-exit: LINK_PORT open to the world"

echo "7. Test on other networks by hand: home ISP + two mobile carriers (and in whitelist mode)."
exit $fail
