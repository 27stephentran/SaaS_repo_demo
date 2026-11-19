#!/usr/bin/env bash
set -euo pipefail

BIN_DIR="${HOME}/.local/bin"
mkdir -p "${BIN_DIR}"

# Ensure ~/.bashrc works in non-interactive shells and includes ~/.local/bin
touch "${HOME}/.bashrc"
sed -i '/case \$- in/,/esac/d' "${HOME}/.bashrc" 2>/dev/null || true
grep -qxF 'export PATH="$HOME/.local/bin:$PATH"' "${HOME}/.bashrc" \
  || echo 'export PATH="$HOME/.local/bin:$PATH"' >> "${HOME}/.bashrc"
export PATH="${BIN_DIR}:${PATH}"

ARCH_RAW="$(uname -m)"
case "${ARCH_RAW}" in
  x86_64|amd64) ARCH="amd64" ;;
  aarch64|arm64) ARCH="arm64" ;;
  *)
    echo "Unsupported architecture: ${ARCH_RAW}" >&2
    exit 1
    ;;
esac

# 1. Supabase CLI
if ! command -v supabase >/dev/null 2>&1; then
  TMP_SUPA="$(mktemp -d)"
  trap 'rm -rf "${TMP_SUPA}"' EXIT
  curl -fsSL "https://github.com/supabase/cli/releases/latest/download/supabase_linux_${ARCH}.tar.gz" \
    -o "${TMP_SUPA}/supabase.tar.gz"
  tar -xzf "${TMP_SUPA}/supabase.tar.gz" -C "${TMP_SUPA}"
  install -m 0755 "${TMP_SUPA}/supabase" "${BIN_DIR}/supabase"
  rm -rf "${TMP_SUPA}"
  trap - EXIT
fi

# 2. Render CLI
if ! command -v render >/dev/null 2>&1; then
  TMP_RENDER="$(mktemp -d)"
  trap 'rm -rf "${TMP_RENDER}"' EXIT
  RENDER_TAG="$(curl -fsSL "https://api.github.com/repos/render-oss/cli/releases/latest" \
    | sed -n 's/.*"tag_name": "\([^"]*\)".*/\1/p')"
  if [ -z "${RENDER_TAG}" ]; then
    echo "Failed to resolve latest render-oss/cli release tag" >&2
    exit 1
  fi
  RENDER_VER="${RENDER_TAG#v}"
  curl -fsSL "https://github.com/render-oss/cli/releases/download/${RENDER_TAG}/cli_${RENDER_VER}_linux_${ARCH}.zip" \
    -o "${TMP_RENDER}/render.zip"
  unzip -qo "${TMP_RENDER}/render.zip" -d "${TMP_RENDER}"
  RENDER_BIN="$(find "${TMP_RENDER}" -type f \( -name 'cli_*' -o -name 'render' \) | head -n 1)"
  install -m 0755 "${RENDER_BIN}" "${BIN_DIR}/render"
  rm -rf "${TMP_RENDER}"
  trap - EXIT
fi

# 3. Vercel CLI (user-space npm prefix, no root required)
if ! command -v vercel >/dev/null 2>&1; then
  NPM_CONFIG_PREFIX="${HOME}/.local" npm install -g --no-fund --no-audit vercel
fi

echo "Verified platform CLIs:"
echo "  supabase: $(supabase --version)"
echo "  render:   $(render --version)"
echo "  vercel:   $(vercel --version | tail -n 1)"
