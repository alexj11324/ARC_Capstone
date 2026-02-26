#!/usr/bin/env bash
set -euo pipefail

# Bootstrap a cloud conda environment for FAST headless pipeline.
# Usage:
#   bash scripts/bootstrap_cloud_fast_env.sh [env_name]
#
# Default env name: hazus_env

ENV_NAME="${1:-hazus_env}"
THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${THIS_DIR}/.." && pwd)"
MINICONDA_DIR="${MINICONDA_DIR:-$HOME/miniconda3}"

echo "[bootstrap] repo root: ${REPO_ROOT}"
echo "[bootstrap] env name : ${ENV_NAME}"
echo "[bootstrap] conda dir: ${MINICONDA_DIR}"

install_miniconda() {
  local os arch installer_url installer_path
  os="$(uname -s)"
  arch="$(uname -m)"

  case "${os}" in
    Linux) os="Linux" ;;
    Darwin) os="MacOSX" ;;
    *)
      echo "[bootstrap][error] unsupported OS: ${os}"
      exit 1
      ;;
  esac

  case "${arch}" in
    x86_64|amd64) arch="x86_64" ;;
    aarch64|arm64)
      if [[ "${os}" == "MacOSX" ]]; then
        arch="arm64"
      else
        arch="aarch64"
      fi
      ;;
    *)
      echo "[bootstrap][error] unsupported architecture: ${arch}"
      exit 1
      ;;
  esac

  installer_url="https://repo.anaconda.com/miniconda/Miniconda3-latest-${os}-${arch}.sh"
  installer_path="/tmp/miniconda_installer_${os}_${arch}.sh"
  echo "[bootstrap] downloading Miniconda from ${installer_url}"
  curl -fsSL "${installer_url}" -o "${installer_path}"

  echo "[bootstrap] installing Miniconda to ${MINICONDA_DIR}"
  bash "${installer_path}" -b -p "${MINICONDA_DIR}"
  rm -f "${installer_path}"
}

if ! command -v conda >/dev/null 2>&1; then
  if [[ -x "${MINICONDA_DIR}/bin/conda" ]]; then
    export PATH="${MINICONDA_DIR}/bin:${PATH}"
  else
    install_miniconda
    export PATH="${MINICONDA_DIR}/bin:${PATH}"
  fi
fi

CONDA_BASE="$(conda info --base)"
source "${CONDA_BASE}/etc/profile.d/conda.sh"

if conda env list | awk '{print $1}' | grep -qx "${ENV_NAME}"; then
  echo "[bootstrap] conda env already exists: ${ENV_NAME}"
else
  echo "[bootstrap] creating conda env: ${ENV_NAME}"
  conda create -y -n "${ENV_NAME}" -c conda-forge python=3.11 pip
fi

echo "[bootstrap] installing core geospatial/runtime deps..."
conda install -y -n "${ENV_NAME}" -c conda-forge \
  gdal rasterio pyarrow pandas utm pyyaml numpy oci-cli jq curl

echo "[bootstrap] installing hazpy (full stack compatibility)..."
if conda install -y -n "${ENV_NAME}" -c nhrap hazpy; then
  echo "[bootstrap] hazpy installed from nhrap channel."
else
  echo "[bootstrap][warn] conda hazpy install failed; trying pip fallback..."
  conda run -n "${ENV_NAME}" python -m pip install --upgrade pip
  conda run -n "${ENV_NAME}" python -m pip install hazpy
fi

echo "[bootstrap] validating imports..."
conda run -n "${ENV_NAME}" python - <<'PY'
import importlib
required = ["osgeo", "rasterio", "pyarrow", "utm", "yaml", "numpy", "pandas"]
optional = ["hazpy"]
missing_required = []
missing_optional = []
for name in required:
    try:
        importlib.import_module(name)
    except Exception:
        missing_required.append(name)
for name in optional:
    try:
        importlib.import_module(name)
    except Exception:
        missing_optional.append(name)
if missing_required:
    raise SystemExit("Missing required modules: " + ", ".join(missing_required))
print("required modules OK")
if missing_optional:
    print("optional modules missing:", ", ".join(missing_optional))
else:
    print("optional modules OK: hazpy")
PY

echo "[bootstrap] validating CLIs..."
conda run -n "${ENV_NAME}" oci --version >/dev/null
echo "[bootstrap] oci CLI OK"

echo "[bootstrap] done."
echo
echo "[next] run pipeline on cloud host:"
echo "conda run -n ${ENV_NAME} python \"${REPO_ROOT}/scripts/fast_e2e_from_oracle.py\" --oci-profile DEFAULT --bucket arc-capstone-processed-parquet --state-scope all --raster-name auto --mode impact-only --max-workers 4 --resume"
