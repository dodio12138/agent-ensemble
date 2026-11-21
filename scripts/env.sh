# Sourced by the project launchers. Resolve paths independently of the caller.
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
RUNTIME="$ROOT/.runtime"
NODE_VERSION="24.22.3"
NODE_HOME="$RUNTIME/node-v$NODE_VERSION"
UV_VERSION="0.11.29"

export UV_NO_CONFIG=1
export UV_MANAGED_PYTHON=1
export UV_PYTHON_INSTALL_DIR="$RUNTIME/python"
export UV_PYTHON_BIN_DIR="$RUNTIME/bin"
export UV_CACHE_DIR="$RUNTIME/uv-cache"
export UV_PROJECT_ENVIRONMENT="$ROOT/.venv"
export npm_config_cache="$RUNTIME/npm-cache"
export npm_config_userconfig="$RUNTIME/npm-user.conf"
export npm_config_globalconfig="$RUNTIME/npm-global.conf"
export PATH="$NODE_HOME/bin:$RUNTIME/bin:$PATH"
cd -- "$ROOT"
