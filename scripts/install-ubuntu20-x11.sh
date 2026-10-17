#!/usr/bin/env bash
# Experimental Ubuntu 20.04 adapter: existing X11/VNC and legacy input backend.
set -Eeuo pipefail
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source /etc/os-release
if [[ "${ID:-}" != ubuntu || "${VERSION_ID:-}" != 20.04 || "$(uname -m)" != x86_64 ]]; then
    printf 'This experimental adapter requires x86-64 Ubuntu 20.04.\n' >&2
    exit 1
fi
if [[ "${XDG_SESSION_TYPE:-}" != x11 ]]; then
    printf 'Run this adapter from a logged-in Ubuntu on Xorg desktop.\n' >&2
    exit 1
fi
export UURB_ALLOW_UNVALIDATED_UBUNTU=1
export UURB_INPUT_ROUTE=legacy
export UURB_RELEASE_MANIFEST="${UURB_RELEASE_MANIFEST:-$repo_dir/patches/uu-remote-4.43.1.3614.json}"
export UURB_FOLLOW_DESKTOP_RESOLUTION="${UURB_FOLLOW_DESKTOP_RESOLUTION:-on}"
exec "$repo_dir/install.sh" "$@" --desktop-relay vnc --keyboard-route x11
