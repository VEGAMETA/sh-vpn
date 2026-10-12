#!/usr/bin/env bash
# Run from WSL: ./ansible.sh playbooks/site.yml [ansible-playbook args]
#               ./ansible.sh -m ping all        (anything starting with -m goes to `ansible`)
set -euo pipefail
cd "$(dirname "$0")"
export ANSIBLE_CONFIG="$PWD/ansible.cfg"
export PATH="$HOME/.local/bin:$PATH"
if [[ "${1:-}" == -m ]]; then exec ansible "$@"; fi
exec ansible-playbook "$@"
