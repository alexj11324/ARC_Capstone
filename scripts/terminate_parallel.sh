#!/usr/bin/env bash
# Terminate all parallel advisory instances
SSH_KEY="$HOME/.ssh/oracle-instance.key"
STATE_DIR="$(cat /tmp/arc_last_state_dir 2>/dev/null)"
[[ -z "$STATE_DIR" || ! -d "$STATE_DIR" ]] && echo "No state dir found." && exit 1

instances=()
for iid_file in "$STATE_DIR"/*.iid; do
  instances+=("$(cat "$iid_file")")
done

echo "Found ${#instances[@]} instances to terminate."
read -rp "Terminate ALL? [y/N] " confirm
[[ "$confirm" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }

for iid in "${instances[@]}"; do
  echo -n "  Terminating $iid ... "
  oci compute instance terminate --instance-id "$iid" --force 2>/dev/null \
    && echo "OK" || echo "FAILED"
done
echo "Done."
