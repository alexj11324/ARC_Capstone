#!/usr/bin/env bash
# ===========================================================================
# launch_cloud_parallel.sh
# Creates one OCI A1.Flex (10 OCPU) instance per advisory, bootstraps,
# and runs the FAST pipeline. bash 3.2 compatible (macOS default).
# Usage: bash scripts/launch_cloud_parallel.sh [--dry-run]
# ===========================================================================
set -euo pipefail

COMPARTMENT="ocid1.tenancy.oc1..aaaaaaaasjyi3owq4d54uq2x6sxlck2kbgpdgji2ijeiwsqmj4qdt7xdyifa"
SUBNET="ocid1.subnet.oc1.iad.aaaaaaaa2zfffkmp5n5v2zwb2yydyrkb27js5hsnnqhfmjsnnwjjdjsb2grq"
IMAGE="ocid1.image.oc1.iad.aaaaaaaa3axglz7hak6fmtcrpfckybc4j7zkausb4xpbqwbfypzfsto2pdmq"
AD="lytI:US-ASHBURN-AD-1"
SHAPE="VM.Standard.A1.Flex"
OCPUS=10
MEMORY_GB=60   # 6 GB per OCPU (OCI minimum ratio)
MAX_WORKERS=10

SSH_KEY_PATH="$HOME/.ssh/oracle-instance.key"
REPO_LOCAL="/Users/alexjiang/Library/Mobile Documents/com~apple~CloudDocs/CMU/course/Capstone/ARC_Capstone"
STATE_DIR="/tmp/arc_launch_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$STATE_DIR"

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

# ── Advisory list: "slug|event|raster" ──────────────────────────────────
ADVISORIES=(
  "debby24_adv18|debby_2024|DEBBY_2024_adv18_e10_ResultMaskRaster.tif"
  "debby24_adv19|debby_2024|DEBBY_2024_adv19_e10_ResultMaskRaster.tif"
  "debby24_adv20|debby_2024|DEBBY_2024_adv20_e10_ResultMaskRaster.tif"
  "flor18_adv63|florence_2018|FLORENCE_2018_adv63_e10_ResultMaskRaster.tif"
  "flor18_adv64|florence_2018|FLORENCE_2018_adv64_e10_ResultMaskRaster.tif"
  "flor18_adv65|florence_2018|FLORENCE_2018_adv65_e10_ResultMaskRaster.tif"
  "hele24_adv14|helene_2024|HELENE_2024_adv14_e10_ResultMaskRaster.tif"
  "hele24_adv15|helene_2024|HELENE_2024_adv15_e10_ResultMaskRaster.tif"
  "hele24_adv16|helene_2024|HELENE_2024_adv16_e10_ResultMaskRaster.tif"
  "ian22_adv31|ian_2022|IAN_2022_adv31_e10_ResultMaskRaster.tif"
  "ian22_adv32|ian_2022|IAN_2022_adv32_e10_ResultMaskRaster.tif"
  "ian22_adv33|ian_2022|IAN_2022_adv33_e10_ResultMaskRaster.tif"
  "idal23_adv18|idalia_2023|IDALIA_2023_adv18_e10_ResultMaskRaster.tif"
  "idal23_adv19|idalia_2023|IDALIA_2023_adv19_e10_ResultMaskRaster.tif"
  "idal23_adv20|idalia_2023|IDALIA_2023_adv20_e10_ResultMaskRaster.tif"
  "ida21_adv16|ida_2021|IDA_2021_adv16_e10_ResultMaskRaster.tif"
  "ida21_adv17|ida_2021|IDA_2021_adv17_e10_ResultMaskRaster.tif"
  "ida21_adv18|ida_2021|IDA_2021_adv18_e10_ResultMaskRaster.tif"
  "mich18_adv20|michael_2018|MICHAEL_2018_adv20_e10_ResultMaskRaster.tif"
  "mich18_adv21|michael_2018|MICHAEL_2018_adv21_e10_ResultMaskRaster.tif"
  "mich18_adv22|michael_2018|MICHAEL_2018_adv22_e10_ResultMaskRaster.tif"
  "milt24_adv20|milton_2024|MILTON_2024_adv20_e10_ResultMaskRaster.tif"
  "milt24_adv21|milton_2024|MILTON_2024_adv21_e10_ResultMaskRaster.tif"
  "milt24_adv22|milton_2024|MILTON_2024_adv22_e10_ResultMaskRaster.tif"
)

N="${#ADVISORIES[@]}"
SSH_PUBKEY="$(ssh-keygen -y -f "$SSH_KEY_PATH" 2>/dev/null)"
GEN_SCRIPT="$REPO_LOCAL/scripts/gen_cloudinit.py"

COST=$(python3 -c "print('%.2f' % ($N * ($OCPUS*0.01 + $MEMORY_GB*0.0015) * 1.5))")

# ── Cloud-init builder (delegates to Python) ────────────────────────────
make_userdata() {
  local event="$1" raster="$2"
  python3 "$GEN_SCRIPT" \
    --event "$event" \
    --raster "$raster" \
    --oci-config "$HOME/.oci/config" \
    --oci-key "$HOME/.oci/oci_api_key.pem"
}

# ── Helpers ──────────────────────────────────────────────────────────────
wait_running() {
  local iid="$1" n=0
  while [[ $n -lt 40 ]]; do
    local s
    s=$(oci compute instance get --instance-id "$iid" \
      --query 'data."lifecycle-state"' --raw-output 2>/dev/null || echo UNKNOWN)
    [[ "$s" == "RUNNING" ]] && return 0
    [[ "$s" == "TERMINATED" || "$s" == "FAILED" ]] && return 1
    sleep 15; (( n++ ))
  done; return 1
}

get_ip() {
  oci compute instance list-vnics \
    --instance-id "$1" --compartment-id "$COMPARTMENT" \
    --query 'data[0]."public-ip"' --raw-output 2>/dev/null
}

# ── Dry-run / confirmation ───────────────────────────────────────────────
echo "================================================================"
echo " ARC Parallel Launch: $N advisories"
echo " Shape: $SHAPE  $OCPUS OCPU / ${MEMORY_GB}GB  (max_workers=$MAX_WORKERS)"
echo " AD: $AD"
echo " Estimated cost: ~\$$COST (trial credits)"
echo "================================================================"
$DRY_RUN && echo "[DRY RUN]" && exit 0

read -rp "Create $N instances now? [y/N] " confirm
[[ "$confirm" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }

# ── Phase 1: Launch all instances ────────────────────────────────────────
echo; echo "=== Phase 1: Creating instances ==="
for entry in "${ADVISORIES[@]}"; do
  IFS='|' read -r slug event raster <<< "$entry"
  display="arc-${slug//_/-}"

  udata_file=$(mktemp /tmp/udata_XXXXXX)
  make_userdata "$event" "$raster" > "$udata_file"

  iid=$(oci compute instance launch \
    --compartment-id "$COMPARTMENT" \
    --availability-domain "$AD" \
    --subnet-id "$SUBNET" \
    --image-id "$IMAGE" \
    --shape "$SHAPE" \
    --shape-config "{\"ocpus\":$OCPUS,\"memoryInGBs\":$MEMORY_GB}" \
    --display-name "$display" \
    --ssh-authorized-keys-file <(echo "$SSH_PUBKEY") \
    --user-data-file "$udata_file" \
    --assign-public-ip true \
    --query 'data.id' --raw-output 2>&1)
  rm -f "$udata_file"

  echo "$iid" > "$STATE_DIR/${slug}.iid"
  echo "[launched] $display -> $iid"
  sleep 2
done

echo; echo "=== Phase 2: Waiting for RUNNING state ==="
for entry in "${ADVISORIES[@]}"; do
  IFS='|' read -r slug event raster <<< "$entry"
  iid="$(cat "$STATE_DIR/${slug}.iid")"
  printf "  %-20s ... " "$slug"
  if wait_running "$iid"; then
    ip="$(get_ip "$iid")"
    echo "$ip" > "$STATE_DIR/${slug}.ip"
    echo "RUNNING @ $ip"
  else
    echo "FAILED"
    echo "FAILED" > "$STATE_DIR/${slug}.ip"
  fi
done

# Save state for monitor/terminate scripts
echo "$STATE_DIR" > /tmp/arc_last_state_dir
echo "[state saved to $STATE_DIR]"

echo; echo "=== Phase 3: Rsyncing repo (parallel) ==="
for entry in "${ADVISORIES[@]}"; do
  IFS='|' read -r slug event raster <<< "$entry"
  ip="$(cat "$STATE_DIR/${slug}.ip" 2>/dev/null || echo FAILED)"
  [[ "$ip" == "FAILED" ]] && continue
  (
    for i in $(seq 1 24); do
      ssh -i "$SSH_KEY_PATH" -o StrictHostKeyChecking=no \
          -o ConnectTimeout=5 ubuntu@"$ip" true 2>/dev/null && break
      sleep 10
    done
    rsync -az \
      --exclude='exports/' --exclude='__pycache__' --exclude='*.pyc' \
      --exclude='.git/' --exclude='Red Cross Capstone Project/' --exclude='*.pdf' \
      -e "ssh -i $SSH_KEY_PATH -o StrictHostKeyChecking=no" \
      "$REPO_LOCAL/" ubuntu@"$ip":~/ARC_Capstone/ 2>/dev/null
    echo "[rsync done] $slug"
  ) &
done
wait
echo "All rsyncs complete."

echo; echo "=== Phase 4: Waiting for bootstrap + starting pipelines ==="
for entry in "${ADVISORIES[@]}"; do
  IFS='|' read -r slug event raster <<< "$entry"
  ip="$(cat "$STATE_DIR/${slug}.ip" 2>/dev/null || echo FAILED)"
  [[ "$ip" == "FAILED" ]] && continue
  (
    # Wait for bootstrap_done (up to 30 min)
    for i in $(seq 1 60); do
      ssh -i "$SSH_KEY_PATH" -o StrictHostKeyChecking=no \
          -o ConnectTimeout=5 ubuntu@"$ip" \
          "test -f /home/ubuntu/bootstrap_done" 2>/dev/null && break
      sleep 30
    done
    # Launch pipeline in screen
    ssh -i "$SSH_KEY_PATH" -o StrictHostKeyChecking=no ubuntu@"$ip" \
      "screen -dmS fast bash -c '
        source /home/ubuntu/miniconda3/etc/profile.d/conda.sh
        conda run -n hazus_env \
          PYTHONUNBUFFERED=1 \
          python /home/ubuntu/ARC_Capstone/scripts/fast_e2e_from_oracle.py \
            --event ${event} \
            --raster-name ${raster} \
            --mode impact-only \
            --no-resume \
            --upload-results \
            --max-workers ${MAX_WORKERS} \
          2>&1 | tee /home/ubuntu/fast.log
        echo DONE > /home/ubuntu/pipeline_done
      '" 2>/dev/null
    echo "[pipeline started] $slug @ $ip"
  ) &
done
wait

echo
echo "================================================================"
echo " All $N pipelines launched!"
echo " Monitor : bash scripts/monitor_parallel.sh"
echo " Terminate: bash scripts/terminate_parallel.sh"
echo "================================================================"
