#!/usr/bin/env python3
"""
Deploy and run all 24 advisories across 6 existing OCI instances.
Each instance runs 4 advisories sequentially.
"""
import subprocess, time, sys, threading, textwrap
from pathlib import Path

SSH_KEY = Path.home() / ".ssh/oracle-instance.key"
REPO_LOCAL = Path("/Users/alexjiang/Library/Mobile Documents/com~apple~CloudDocs/CMU/course/Capstone/ARC_Capstone")
MAX_WORKERS = 10

# ── 6 instances and their assigned advisories ────────────────────────────
INSTANCES = [
    {
        "ip": "129.159.112.145",
        "label": "debby18",
        "advisories": [
            ("debby_2024",    "DEBBY_2024_adv18_e10_ResultMaskRaster.tif"),
            ("debby_2024",    "DEBBY_2024_adv19_e10_ResultMaskRaster.tif"),
            ("debby_2024",    "DEBBY_2024_adv20_e10_ResultMaskRaster.tif"),
            ("florence_2018", "FLORENCE_2018_adv63_e10_ResultMaskRaster.tif"),
        ],
    },
    {
        "ip": "129.158.44.200",
        "label": "debby19",
        "advisories": [
            ("florence_2018", "FLORENCE_2018_adv64_e10_ResultMaskRaster.tif"),
            ("florence_2018", "FLORENCE_2018_adv65_e10_ResultMaskRaster.tif"),
            ("helene_2024",   "HELENE_2024_adv14_e10_ResultMaskRaster.tif"),
            ("helene_2024",   "HELENE_2024_adv15_e10_ResultMaskRaster.tif"),
        ],
    },
    {
        "ip": "129.153.228.32",
        "label": "debby20",
        "advisories": [
            ("helene_2024",   "HELENE_2024_adv16_e10_ResultMaskRaster.tif"),
            ("ian_2022",      "IAN_2022_adv31_e10_ResultMaskRaster.tif"),
            ("ian_2022",      "IAN_2022_adv32_e10_ResultMaskRaster.tif"),
            ("ian_2022",      "IAN_2022_adv33_e10_ResultMaskRaster.tif"),
        ],
    },
    {
        "ip": "193.122.193.127",
        "label": "flor63",
        "advisories": [
            ("idalia_2023",   "IDALIA_2023_adv18_e10_ResultMaskRaster.tif"),
            ("idalia_2023",   "IDALIA_2023_adv19_e10_ResultMaskRaster.tif"),
            ("idalia_2023",   "IDALIA_2023_adv20_e10_ResultMaskRaster.tif"),
            ("ida_2021",      "IDA_2021_adv16_e10_ResultMaskRaster.tif"),
        ],
    },
    {
        "ip": "129.158.220.31",
        "label": "flor64",
        "advisories": [
            ("ida_2021",      "IDA_2021_adv17_e10_ResultMaskRaster.tif"),
            ("ida_2021",      "IDA_2021_adv18_e10_ResultMaskRaster.tif"),
            ("michael_2018",  "MICHAEL_2018_adv20_e10_ResultMaskRaster.tif"),
            ("michael_2018",  "MICHAEL_2018_adv21_e10_ResultMaskRaster.tif"),
        ],
    },
    {
        "ip": "129.158.227.43",
        "label": "flor65",
        "advisories": [
            ("michael_2018",  "MICHAEL_2018_adv22_e10_ResultMaskRaster.tif"),
            ("milton_2024",   "MILTON_2024_adv20_e10_ResultMaskRaster.tif"),
            ("milton_2024",   "MILTON_2024_adv21_e10_ResultMaskRaster.tif"),
            ("milton_2024",   "MILTON_2024_adv22_e10_ResultMaskRaster.tif"),
        ],
    },
]

def ssh(ip, cmd, check=False, timeout=10):
    return subprocess.run(
        ["ssh", "-i", str(SSH_KEY),
         "-o", "StrictHostKeyChecking=no",
         "-o", f"ConnectTimeout={timeout}",
         f"ubuntu@{ip}", cmd],
        capture_output=True, text=True, check=check
    )

def wait_ssh(ip, label, max_wait=600):
    print(f"[{label}] waiting for SSH...")
    deadline = time.time() + max_wait
    while time.time() < deadline:
        r = ssh(ip, "true")
        if r.returncode == 0:
            print(f"[{label}] SSH ready")
            return True
        time.sleep(15)
    print(f"[{label}] SSH timeout!")
    return False

def wait_bootstrap(ip, label, max_wait=1800):
    print(f"[{label}] waiting for bootstrap...")
    deadline = time.time() + max_wait
    while time.time() < deadline:
        r = ssh(ip, "test -f /home/ubuntu/bootstrap_done && echo yes || echo no")
        if r.returncode == 0 and "yes" in r.stdout:
            print(f"[{label}] bootstrap done")
            return True
        time.sleep(30)
    print(f"[{label}] bootstrap timeout!")
    return False

def rsync_repo(ip, label):
    print(f"[{label}] rsyncing repo...")
    r = subprocess.run(
        ["rsync", "-az",
         "--exclude=exports/", "--exclude=__pycache__",
         "--exclude=*.pyc", "--exclude=.git/",
         "--exclude=Red Cross Capstone Project/", "--exclude=*.pdf",
         "-e", f"ssh -i {SSH_KEY} -o StrictHostKeyChecking=no",
         f"{REPO_LOCAL}/",
         f"ubuntu@{ip}:~/ARC_Capstone/"],
        capture_output=True, text=True
    )
    if r.returncode == 0:
        print(f"[{label}] rsync done")
    else:
        print(f"[{label}] rsync FAILED: {r.stderr[:200]}")
    return r.returncode == 0

def build_run_script(advisories):
    """Generate a bash script that runs N advisories sequentially."""
    lines = [
        "#!/bin/bash",
        "set -euo pipefail",
        "source /home/ubuntu/miniconda3/etc/profile.d/conda.sh",
        f"PYTHON=/home/ubuntu/miniconda3/envs/hazus_env/bin/python",
        f"SCRIPT=/home/ubuntu/ARC_Capstone/scripts/fast_e2e_from_oracle.py",
        "",
        "run_adv() {",
        "  local event=$1 raster=$2",
        '  echo "[$(date)] === START $event / $raster ==="',
        "  PYTHONUNBUFFERED=1 conda run -n hazus_env python $SCRIPT \\",
        f"    --mode impact-only --no-resume --upload-results --max-workers {MAX_WORKERS} \\",
        "    --event \"$event\" --raster-name \"$raster\"",
        '  echo "[$(date)] === DONE $event / $raster ==="',
        "}",
        "",
    ]
    for event, raster in advisories:
        lines.append(f'run_adv "{event}" "{raster}"')
    lines.append("")
    lines.append("echo ALL_DONE > /home/ubuntu/all_done")
    return "\n".join(lines)

def deploy_instance(inst):
    ip = inst["ip"]
    label = inst["label"]
    advisories = inst["advisories"]

    if not wait_ssh(ip, label):
        return
    if not wait_bootstrap(ip, label):
        return
    if not rsync_repo(ip, label):
        return

    # Write run script to instance
    script = build_run_script(advisories)
    write_r = ssh(ip, f"cat > /home/ubuntu/run_all.sh << 'ENDSCRIPT'\n{script}\nENDSCRIPT")
    # Use a more reliable method
    r = subprocess.run(
        ["ssh", "-i", str(SSH_KEY), "-o", "StrictHostKeyChecking=no",
         f"ubuntu@{ip}",
         f"cat > /home/ubuntu/run_all.sh"],
        input=script, capture_output=True, text=True
    )
    ssh(ip, "chmod +x /home/ubuntu/run_all.sh")

    # Start in screen
    ssh(ip, "screen -dmS arc_run bash /home/ubuntu/run_all.sh")
    print(f"[{label}] pipeline started — {len(advisories)} advisories queued")
    adv_names = [r.replace("_e10_ResultMaskRaster.tif","") for _, r in advisories]
    print(f"[{label}]   -> {', '.join(adv_names)}")

def main():
    print("=" * 60)
    print(f" Deploying to 6 instances, 4 advisories each (24 total)")
    print(f" max_workers={MAX_WORKERS} per pipeline run")
    print("=" * 60)

    threads = []
    for inst in INSTANCES:
        t = threading.Thread(target=deploy_instance, args=(inst,), daemon=True)
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    print("\n" + "=" * 60)
    print(" All instances deployed. Monitor with:")
    print("   python3 scripts/deploy_to_instances.py --status")
    print("=" * 60)

def status():
    print(f"{'Instance':<12} {'IP':<18} {'Bootstrap':<12} {'Done':<6} Last log line")
    print("-" * 90)
    for inst in INSTANCES:
        ip, label = inst["ip"], inst["label"]
        boot = ssh(ip, "test -f ~/bootstrap_done && echo yes || echo no").stdout.strip()
        all_done = ssh(ip, "cat ~/all_done 2>/dev/null || echo -").stdout.strip()
        last = ssh(ip, "tail -1 ~/run_all_out.log 2>/dev/null || echo -").stdout.strip()
        # Check current screen log
        last2 = ssh(ip, "grep -oP '\\[\\d{4}-\\d{2}-\\d{2}.*' ~/ARC_Capstone/exports/*/reports/run_manifest.json 2>/dev/null | tail -1 || echo -").stdout.strip()
        done_flag = "YES" if all_done == "ALL_DONE" else "-"
        print(f"{label:<12} {ip:<18} {boot:<12} {done_flag:<6} {last[:40]}")

if __name__ == "__main__":
    if "--status" in sys.argv:
        status()
    else:
        main()
