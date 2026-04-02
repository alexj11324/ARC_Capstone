import json
from pathlib import Path
import sys

REPO_DIR = Path(__file__).resolve().parent

with open(REPO_DIR / "notebooks/shelter_demand.ipynb") as f:
    nb = json.load(f)

# Evaluate all in ONE continuous context
exec_ctx = {'REPO_DIR': REPO_DIR}

for cell in nb['cells']:
    if cell['cell_type'] == 'code':
        source = "".join(cell['source'])
        print(f"Executing Cell ...")
        
        try:
            exec(source, exec_ctx)
        except Exception as e:
            if "pip install" in source:
                pass # skip pip install errors if they happen in fast test env
            else:
                raise e
        
        # Stop after we validate the params are set successfully
        if 'params' in exec_ctx:
            print("\n✅ Successfully extracted params at runtime!")
            print("storm_id:", exec_ctx['params'].get('storm_id'))
            print("year:", exec_ctx['params'].get('year'))
            print("TRACT_SEVERITY:", exec_ctx['params'].get('TRACT_SEVERITY'))
            sys.exit(0)
