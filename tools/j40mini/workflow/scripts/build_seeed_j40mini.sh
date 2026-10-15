#!/usr/bin/env bash
# Build the selected Seeed kernel, modules and DTBs locally. Does not flash hardware.
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/seeed_j40mini_env.sh"
[[ $# == 0 ]] || { echo "Usage: bash $0" >&2; exit 2; }
[[ -x "${CROSS_COMPILE}gcc" && -f "${SOURCE}/nvbuild.sh" ]] || {
    echo "Prepare the matching R${SEEED_L4T_VERSION} source and toolchain first." >&2; exit 1;
}
mkdir -p "${WORK}/logs"
cd "${SOURCE}"
rm -f "${WORK}/.compiled"
./nvbuild.sh 2>&1 | tee "${WORK}/logs/nvbuild.log"
touch "${WORK}/.compiled"
