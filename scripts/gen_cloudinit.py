#!/usr/bin/env python3
"""Generate base64-encoded cloud-init user-data for an OCI advisory instance."""
import argparse, base64, pathlib, sys

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--event",  required=True)
    p.add_argument("--raster", required=True)
    p.add_argument("--oci-config", required=True,
                   help="Path to ~/.oci/config")
    p.add_argument("--oci-key",    required=True,
                   help="Path to ~/.oci/oci_api_key.pem")
    args = p.parse_args()

    oci_cfg = pathlib.Path(args.oci_config).read_text()
    # Fix key_file path to server path
    oci_cfg_fixed = "\n".join(
        "key_file=/home/ubuntu/.oci/oci_api_key.pem" if l.startswith("key_file=") else l
        for l in oci_cfg.splitlines()
    )
    oci_key_b64 = base64.b64encode(
        pathlib.Path(args.oci_key).read_bytes()
    ).decode()

    script = f"""\
#!/bin/bash
set -euo pipefail
exec >>/home/ubuntu/cloud_init.log 2>&1
echo "[cloud-init] started $(date)"

# ── OCI credentials ──────────────────────────────────────────────────────
mkdir -p /home/ubuntu/.oci
chmod 700 /home/ubuntu/.oci

cat > /home/ubuntu/.oci/config << 'OCICFG'
{oci_cfg_fixed}
OCICFG

echo '{oci_key_b64}' | base64 -d > /home/ubuntu/.oci/oci_api_key.pem
chmod 600 /home/ubuntu/.oci/oci_api_key.pem /home/ubuntu/.oci/config
chown -R ubuntu:ubuntu /home/ubuntu/.oci

# ── Miniconda (aarch64) ──────────────────────────────────────────────────
curl -fsSL https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-aarch64.sh \
  -o /tmp/mc.sh
sudo -u ubuntu bash /tmp/mc.sh -b -p /home/ubuntu/miniconda3

export PATH=/home/ubuntu/miniconda3/bin:$PATH

sudo -u ubuntu /home/ubuntu/miniconda3/bin/conda tos accept \
  --override-channels --channel https://repo.anaconda.com/pkgs/main
sudo -u ubuntu /home/ubuntu/miniconda3/bin/conda tos accept \
  --override-channels --channel https://repo.anaconda.com/pkgs/r

# ── Create hazus_env ─────────────────────────────────────────────────────
sudo -u ubuntu /home/ubuntu/miniconda3/bin/conda create \
  -y -n hazus_env -c conda-forge python=3.11 pip

sudo -u ubuntu /home/ubuntu/miniconda3/bin/conda install \
  -y -n hazus_env -c conda-forge \
  gdal rasterio pyarrow pandas utm pyyaml numpy oci-cli

sudo -u ubuntu /home/ubuntu/miniconda3/bin/conda run \
  -n hazus_env pip install hazpy

echo "[cloud-init] bootstrap done $(date)"
touch /home/ubuntu/bootstrap_done

echo '{args.event}'  > /home/ubuntu/run_event
echo '{args.raster}' > /home/ubuntu/run_raster
"""
    sys.stdout.write(base64.b64encode(script.encode()).decode())

if __name__ == "__main__":
    main()
