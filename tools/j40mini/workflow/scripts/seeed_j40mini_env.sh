# Shared paths for the standalone R39.2.1 workflow workspace.
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SEEED_L4T_VERSION="${SEEED_L4T_VERSION:-39.2.1}"
[[ ${SEEED_L4T_VERSION} == 39.2.1 ]] || {
    echo 'Unsupported Seeed L4T version.' >&2; return 1;
}
WORK="${PROJECT_DIR}/work/seeed-j40mini-r${SEEED_L4T_VERSION}"
SOURCE="${WORK}/Linux_for_Tegra/source"
HOST_TOOLS="${WORK}/host-tools/usr"
export ARCH=arm64
export CROSS_COMPILE="${CROSS_COMPILE:-${WORK}/l4t-gcc/aarch64--glibc--stable-2022.08-1/bin/aarch64-buildroot-linux-gnu-}"
export PATH="${HOST_TOOLS}/bin:${PATH}"
export BISON_PKGDATADIR="${HOST_TOOLS}/share/bison"
export HOSTCFLAGS="-I${HOST_TOOLS}/include ${HOSTCFLAGS:-}"
export HOSTLDFLAGS="-L${HOST_TOOLS}/lib/x86_64-linux-gnu ${HOSTLDFLAGS:-}"
export NPROC="${NPROC:-24}"
