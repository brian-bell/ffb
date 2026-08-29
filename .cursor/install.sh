#!/usr/bin/env bash
# Idempotent Cloud Agent bootstrap: uv, tracker Node/npm pins, then lockfile installs.
# Verification gates and live ingest are not part of install. No secrets required.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
if [[ ! -s "$NVM_DIR/nvm.sh" ]]; then
  echo "cloud-agent-install: nvm not found at $NVM_DIR" >&2
  exit 1
fi
# shellcheck disable=SC1091
. "$NVM_DIR/nvm.sh"

mkdir -p "$HOME/.local/bin"

# Cursor's default image puts /usr/local/cargo/bin first on PATH, ahead of nvm
# and /exec-daemon's Node 22. Pin shims there so later agent shells see uv/Node 24.
# Install exports do not persist; a missing or unwritable shim dir would leave
# later shells on the image Node 22.
shim_dir="/usr/local/cargo/bin"
if [[ ! -d "$shim_dir" || ! -w "$shim_dir" ]]; then
  echo "cloud-agent-install: $shim_dir is missing or not writable; cannot pin uv/Node 24 ahead of the image Node 22" >&2
  exit 1
fi

link_tool() {
  local src="$1" name="$2"
  local local_dest="$HOME/.local/bin/$name"
  if [[ "$src" != "$local_dest" ]]; then
    ln -sfn "$src" "$local_dest"
  fi
  ln -sfn "$src" "$shim_dir/$name"
}

echo "cloud-agent-install: installing uv"
curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 UV_INSTALL_DIR="$HOME/.local/bin" sh
link_tool "$HOME/.local/bin/uv" uv
if [[ -e "$HOME/.local/bin/uvx" ]]; then
  link_tool "$HOME/.local/bin/uvx" uvx
fi

node_version="$(tr -d '[:space:]' < tracker/.nvmrc)"
if [[ -z "$node_version" ]]; then
  echo "cloud-agent-install: tracker/.nvmrc is empty" >&2
  exit 1
fi

npm_spec="$(python3 -c 'import json; print(json.load(open("tracker/package.json"))["packageManager"])')"
case "$npm_spec" in
  npm@*) npm_version="${npm_spec#npm@}" ;;
  *)
    echo "cloud-agent-install: unexpected tracker packageManager: $npm_spec" >&2
    exit 1
    ;;
esac

echo "cloud-agent-install: installing Node $node_version"
nvm install "$node_version"
nvm alias default "$node_version"
nvm use "$node_version"

node_bin="$(dirname "$(nvm which "$node_version")")"
export PATH="$node_bin:$PATH"
hash -r 2>/dev/null || true

if [[ "$(npm --version)" != "$npm_version" ]]; then
  echo "cloud-agent-install: installing npm $npm_version"
  if command -v corepack >/dev/null 2>&1; then
    corepack enable
    corepack prepare "npm@${npm_version}" --activate
  else
    npm install -g "npm@${npm_version}"
  fi
fi

for cmd in node npm npx corepack; do
  if [[ -e "$node_bin/$cmd" ]]; then
    link_tool "$node_bin/$cmd" "$cmd"
  fi
done

export PATH="$shim_dir:$HOME/.local/bin:$PATH"
hash -r 2>/dev/null || true

echo "cloud-agent-install: uv sync --frozen"
uv python install
uv sync --frozen

echo "cloud-agent-install: npm ci --prefix tracker"
npm ci --prefix tracker

echo "cloud-agent-install: python=$(uv run python --version 2>&1) node=$(node --version) npm=$(npm --version)"
