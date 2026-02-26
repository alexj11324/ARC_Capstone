#!/usr/bin/env bash
# Monitor all parallel advisory runs
SSH_KEY="$HOME/.ssh/oracle-instance.key"
STATE_DIR="$(cat /tmp/arc_last_state_dir 2>/dev/null)"
[[ -z "$STATE_DIR" || ! -d "$STATE_DIR" ]] && echo "No state dir found." && exit 1

echo "================================================================"
echo " ARC Monitor  $(date)  [$(basename "$STATE_DIR")]"
echo "================================================================"
printf "%-22s %-16s %-12s %s\n" "Advisory" "IP" "Status" "Last log line"
echo "----------------------------------------------------------------"

done_count=0
total=0
for iid_file in "$STATE_DIR"/*.iid; do
  slug="$(basename "$iid_file" .iid)"
  ip="$(cat "$STATE_DIR/${slug}.ip" 2>/dev/null || echo '')"
  [[ -z "$ip" || "$ip" == "FAILED" ]] && \
    printf "%-22s %-16s %-12s\n" "$slug" "-" "FAILED" && continue
  (( total++ ))

  bootstrap=$(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=4 \
    ubuntu@"$ip" "test -f ~/bootstrap_done && echo yes || echo no" 2>/dev/null || echo "?")
  done_flag=$(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=4 \
    ubuntu@"$ip" "cat ~/pipeline_done 2>/dev/null || echo -n" 2>/dev/null || echo "")
  tail_line=$(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=4 \
    ubuntu@"$ip" "tail -1 ~/fast.log 2>/dev/null || echo '—'" 2>/dev/null || echo "ssh err")

  if [[ "$done_flag" == "DONE" ]]; then
    status="DONE ✓"; (( done_count++ ))
  elif [[ "$bootstrap" == "yes" ]]; then
    status="RUNNING"
  elif [[ "$bootstrap" == "no" ]]; then
    status="BOOTING"
  else
    status="UNREACHABLE"
  fi

  printf "%-22s %-16s %-12s %s\n" "$slug" "$ip" "$status" "${tail_line:0:48}"
done
echo "----------------------------------------------------------------"
echo " Done: $done_count / $total"
echo "================================================================"
