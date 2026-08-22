#!/usr/bin/env bash
# Regresión: fstab swap no debe duplicarse (bug que colgaba el arranque).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../scripts/lib/common.sh
source "${ROOT}/scripts/lib/common.sh"

SWAP_FILE="/var/swap"
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

FSTAB="${TMP}/fstab"
cat >"${FSTAB}" <<'EOF'
proc            /proc           proc    defaults          0       0
PARTUUID=7c87d4cb-01  /boot/firmware  vfat    defaults          0       2
PARTUUID=7c87d4cb-02  /               ext4    defaults,noatime  0       1
/var/swap none swap sw 0 0
/var/swap none swap sw 0 0
/var/swap none swap sw 0 0
EOF

# Ejecutar dedupe (misma lógica que ensure-host-swap.sh)
count="$(grep -cE "^[[:space:]]*${SWAP_FILE}[[:space:]]" "${FSTAB}" || echo 0)"
if [[ "${count}" -le 1 ]]; then
  echo "FAIL: fixture debería tener entradas duplicadas" >&2
  exit 1
fi

tmp_out="$(mktemp)"
awk -v swap="${SWAP_FILE}" '
  $1 == swap { if (seen++) next }
  { print }
' "${FSTAB}" >"${tmp_out}"
mv "${tmp_out}" "${FSTAB}"

count_after="$(grep -cE "^[[:space:]]*${SWAP_FILE}[[:space:]]" "${FSTAB}" || echo 0)"
if [[ "${count_after}" -ne 1 ]]; then
  echo "FAIL: tras dedupe debe quedar 1 entrada, hay ${count_after}" >&2
  exit 1
fi

# Patrón de detección (inicio de línea, no espacio previo obligatorio)
if grep -qE "^[[:space:]]*${SWAP_FILE}[[:space:]]" "${FSTAB}"; then
  :
else
  echo "FAIL: fstab_has_swap_entry no detectaría la línea existente" >&2
  exit 1
fi

# Simular ensure_fstab_entry: no debe añadir otra línea
if grep -qE "^[[:space:]]*${SWAP_FILE}[[:space:]]" "${FSTAB}"; then
  :
else
  echo "/var/swap none swap sw,nofail 0 0" >>"${FSTAB}"
fi
count_final="$(grep -cE "^[[:space:]]*${SWAP_FILE}[[:space:]]" "${FSTAB}" || echo 0)"
if [[ "${count_final}" -ne 1 ]]; then
  echo "FAIL: ensure_fstab_entry simulado duplicó (count=${count_final})" >&2
  exit 1
fi

echo "OK test_ensure_host_swap"
