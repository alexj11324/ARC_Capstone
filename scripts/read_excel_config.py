import re
import pandas as pd
from pathlib import Path

def get_default_params():
    """Returns the hardcoded baseline configuration (fallbacks)."""
    return {
        # ── Storm inputs ──────────────────────────────────────────
        'storm_id': 'AL022024',
        'storm_name': 'BERYL',
        'advisory': 29,
        'year': 2024,

        # ── FAST engine ───────────────────────────────────────────
        'flood_load_condition': 'CoastalA',  # CoastalA | CoastalV | Riverine
        'fast_timeout': 1800,                # FAST subprocess timeout (seconds)

        # ── Damage classification (Hazus standard defaults) ───────
        'DAMAGE_STATE_THRESHOLDS': {
            'Slight':    (0, 15),
            'Moderate':  (15, 40),
            'Extensive': (40, 60),
            'Complete':  (60, 100),
        },

        # ── Tract severity (ARC Figure 10 thresholds) ────────────
        'TRACT_SEVERITY': {
            'high':   {'pct_destroyed': 0.35, 'pct_major_damage': 0.35},
            'medium': {'pct_destroyed': 0.11, 'pct_major_damage': 0.16},
        },

        # ── Building Habitability Index (BHI) ─────────────────────
        'BLDNG_USABILITY': {
            'Slight':    {'FU': 1.00, 'PU': 0.00, 'NU': 0.00},
            'Moderate':  {'FU': 0.87, 'PU': 0.13, 'NU': 0.00},
            'Extensive': {'FU': 0.25, 'PU': 0.50, 'NU': 0.25},
            'Complete':  {'FU': 0.00, 'PU': 0.02, 'NU': 0.98},
        },

        # ── Utility Loss Severity — [low, high] ranges ───────────
        'UL_SEVERITY': {
            'low':    {'FU': [0.00, 0.05], 'PU': [0.05, 0.10]},
            'medium': {'FU': [0.00, 0.10], 'PU': [0.30, 0.50]},
            'high':   {'FU': [0.10, 0.30], 'PU': [0.60, 0.80]},
        },

        # ── SVI configuration ──────────────────────────────────────
        'SVI_SHELTER_RATES': [0.000, 0.025, 0.050],
        'SVI_BINS': [0.4, 0.8],          # bin edges for low/med/high SVI

        # ── Network / performance ──────────────────────────────────
        'download_timeout': 60,           # raster + census API timeout (s)
        'svi_timeout': 300,               # CDC SVI download timeout (s)
        'census_max_workers': 6,          # concurrent census API threads (positive int)
        'svi_rest_page_size': 2000,       # CDC REST API records per page (positive int)

        # ── Output file names (written under WORK_DIR) ────────────
        'output_csv_name': 'shelter_demand_output.csv',
        'output_xlsx_name': 'shelter_demand_output.xlsx',
    }

def nan_to_none(val):
    if pd.isna(val):
        return None
    return val

def parse_range_pct(val):
    """Parse string range format like '0.11 - 0.34' into the lower bound float 0.11"""
    if pd.isna(val):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        # Extract first decimal number
        match = re.search(r'([\d\.]+)', val)
        if match:
            v = float(match.group(1))
            # If the excel input was entered as '11 - 34' integer percent instead of '0.11 - 0.34', normalize it
            if v > 1.0:
                return v / 100.0
            return v
    return None

def deep_update(d, u):
    for k, v in u.items():
        if isinstance(v, dict) and k in d and isinstance(d[k], dict):
            deep_update(d[k], v)
        else:
            d[k] = v
    return d

def load_config_from_excel(xlsx_path):
    """
    Reads the 'Interface' sheet of the Excel configuration file.
    Merges non-NaN values over the default param fallbacks.
    """
    params = get_default_params()
    
    xlsx_path = Path(xlsx_path)
    if not xlsx_path.exists():
        print(f"Warning: Config file {xlsx_path} not found. Continuing with default parameters.")
        return params

    try:
        df = pd.read_excel(xlsx_path, sheet_name='Interface', header=None)
    except Exception as e:
        print(f"Warning: Failed to read {xlsx_path} ({e}). Continuing with default parameters.")
        return params

    extracted = {}

    # ── Storm inputs ──
    v = nan_to_none(df.iloc[5, 2])
    if v is not None: extracted['storm_id'] = str(v)
        
    v = nan_to_none(df.iloc[6, 2])
    if v is not None: extracted['storm_name'] = str(v)
        
    v = nan_to_none(df.iloc[7, 2])
    if v is not None: extracted['advisory'] = int(v)
        
    v = nan_to_none(df.iloc[8, 2])
    if v is not None: extracted['year'] = int(v)

    # ── Tract severity ──
    try:
        tract_severity = {}
        
        h_d = parse_range_pct(df.iloc[12, 2])
        h_m = parse_range_pct(df.iloc[12, 3])
        if h_d is not None and h_m is not None:
            tract_severity['high'] = {'pct_destroyed': h_d, 'pct_major_damage': h_m}
            
        m_d = parse_range_pct(df.iloc[13, 2])
        m_m = parse_range_pct(df.iloc[13, 3])
        if m_d is not None and m_m is not None:
            tract_severity['medium'] = {'pct_destroyed': m_d, 'pct_major_damage': m_m}
            
        if tract_severity:
            extracted['TRACT_SEVERITY'] = tract_severity
    except IndexError:
        pass # Handle case where user truncates the file further

    # ── SVI Shelter Rates ──
    try:
        # SVI rates moved to rows 17, 18, 19
        if not pd.isna(df.iloc[17, 3]):
            extracted['SVI_SHELTER_RATES'] = [
                float(df.iloc[17, 3]),
                float(df.iloc[18, 3]),
                float(df.iloc[19, 3])
            ]
    except IndexError:
        pass

    # Combine extracted data cleanly over the baseline defaults
    return deep_update(params, extracted)

if __name__ == '__main__':
    # Quick sanity test if run manually
    test_path = Path(__file__).parent.parent / "data" / "ARC Storm Surge Shelter Demand.xlsx"
    p = load_config_from_excel(test_path)
    import pprint
    pprint.pprint(p)
