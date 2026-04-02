import json
import re
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent
NOTEBOOK_PATH = REPO_DIR / "notebooks" / "shelter_demand.ipynb"

new_params_str = """# Cell 2: Configuration 
# =========================================================
# Populates variables by dynamically reading the Interface sheet of the Excel config file.

from scripts.read_excel_config import load_config_from_excel

excel_config_path = REPO_DIR / "data" / "ARC Storm Surge Shelter Demand.xlsx"
params = load_config_from_excel(excel_config_path)"""

with open(NOTEBOOK_PATH, 'r', encoding='utf-8') as f:
    nb = json.load(f)

for idx, cell in enumerate(nb['cells']):
    if cell['cell_type'] == 'code':
        src = "".join(cell['source'])
        if "params = {" in src and "# ── Validate ──" in src:
            new_src = re.sub(
                r'# Cell 2: Configuration.*?params\s*=\s*\{.*?\}(?=\n\n# ── Validate ──)',
                new_params_str,
                src,
                flags=re.DOTALL
            )
            
            lines = [line + '\n' for line in new_src.split('\n')]
            if lines and lines[-1] == '\n':
                lines.pop()
            
            nb['cells'][idx]['source'] = lines
            print(f"Updated Cell {idx}")
            break

with open(NOTEBOOK_PATH, 'w', encoding='utf-8') as f:
    json.dump(nb, f, indent=1)

print("Notebook updated.")
