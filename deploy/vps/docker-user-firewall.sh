#!/usr/bin/env bash
# Docker-aware inbound filter. Docker publishes container ports with DNAT in the nat table, so that
# traffic goes through the FORWARD chain and never reaches UFW's INPUT rules -- UFW alone does NOT
# protect published container ports (found during Phase 4B.1: a staging container on 8080 was
# publicly reachable while `ufw status` only allowed 22). DOCKER-USER is the chain Docker reserves
# for exactly this and evaluates before its own rules.
#
# Policy for traffic arriving from the internet interface to ANY container:
#   allow replies to connections containers opened (ACME, outbound APIs)
#   allow new connections whose ORIGINAL destination port was 80 or 443 (Caddy)
#   drop everything else -- so even an accidental `ports: "5432:5432"` / "8000:8000" stays private.
# Container-to-container and host-to-container traffic (not via EXT_IF) is unaffected.
# SSH is host INPUT traffic, not FORWARD, so this cannot lock anyone out of port 22.
#
# Installed root-owned as /usr/local/sbin/donchian-docker-firewall.sh (NOT under /opt/donchian, which the
# deploy user can write -- a root-run script there would be a privilege-escalation path).
# Idempotent; applied at boot and after every Docker restart by donchian-docker-firewall.service.
set -euo pipefail

EXT_IF="${EXT_IF:-$(ip route show default | awk '/default/ {print $5; exit}')}"
[ -n "$EXT_IF" ] || { echo "could not determine the external interface" >&2; exit 1; }

apply() {
  local ipt="$1"
  "$ipt" -N DOCKER-USER 2>/dev/null || true
  "$ipt" -F DOCKER-USER
  "$ipt" -A DOCKER-USER -i "$EXT_IF" -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
  "$ipt" -A DOCKER-USER -i "$EXT_IF" -p tcp -m conntrack --ctstate NEW --ctorigdstport 80 -j RETURN
  "$ipt" -A DOCKER-USER -i "$EXT_IF" -p tcp -m conntrack --ctstate NEW --ctorigdstport 443 -j RETURN
  "$ipt" -A DOCKER-USER -i "$EXT_IF" -j DROP
  "$ipt" -A DOCKER-USER -j RETURN
}

apply iptables
if ip6tables -L DOCKER-USER -n >/dev/null 2>&1; then
  apply ip6tables
fi
echo "DOCKER-USER filter applied on $EXT_IF (public: tcp/80, tcp/443 only)"
