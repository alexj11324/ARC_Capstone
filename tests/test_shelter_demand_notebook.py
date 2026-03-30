"""Regression tests for the shelter demand Colab notebook."""

from __future__ import annotations

import json
from pathlib import Path


NOTEBOOK_PATH = Path(__file__).resolve().parent.parent / "notebooks" / "shelter_demand.ipynb"


def _code_cells() -> list[str]:
    notebook = json.loads(NOTEBOOK_PATH.read_text(encoding="utf-8"))
    return ["".join(cell.get("source", [])) for cell in notebook["cells"] if cell["cell_type"] == "code"]


def test_notebook_restores_fast_input_preparation_cell() -> None:
    """The FAST runner depends on a prior cell that materializes inventory + tract join inputs."""
    code_cells = _code_cells()

    prep_cell = next(
        (cell for cell in code_cells if cell.startswith("# Cell 5: Spatial Filter + FAST Input Preparation")),
        None,
    )

    assert prep_cell is not None, "Notebook is missing Cell 5: Spatial Filter + FAST Input Preparation"
    assert "fast_csv_path = str(WORK_DIR / 'fast_input.csv')" in prep_cell
    assert "nsi_cbfips_join_path = str(WORK_DIR / 'nsi_cbfips_join.csv')" in prep_cell
    assert "nsi_filtered = (" in prep_cell


def test_notebook_reads_persisted_cbfips_join_for_predictions() -> None:
    """Cell 7 should read the persisted join table instead of relying on cross-cell dataframes."""
    code_cells = _code_cells()

    load_predictions_cell = next(
        (cell for cell in code_cells if cell.startswith("# Cell 7: Load Predictions + Derive Census Tract GEOID")),
        None,
    )

    assert load_predictions_cell is not None
    assert "_nsi_join = WORK_DIR / 'nsi_cbfips_join.csv'" in load_predictions_cell
    assert "pd.read_csv(_nsi_join, dtype={'fltyid': str, 'cbfips': str})" in load_predictions_cell


def test_notebook_tail_cells_guard_cross_cell_dependencies() -> None:
    """Later notebook cells should bind cross-cell state explicitly for notebook linters."""
    code_cells = _code_cells()

    census_cell = next(
        (cell for cell in code_cells if cell.startswith("# Cell 11: Load Census Population + SVI (Tract Level)")),
        None,
    )
    assert census_cell is not None
    assert "_required_globals = ['WORK_DIR', 'tract_agg', 'params']" in census_cell
    assert "WORK_DIR = Path(globals()['WORK_DIR'])" in census_cell
    assert "tract_agg = globals()['tract_agg']" in census_cell
    assert "params = globals()['params']" in census_cell

    summary_cell = next(
        (cell for cell in code_cells if cell.startswith("# Cell 12: Compute Shelter-Seeking Population + Overview")),
        None,
    )
    assert summary_cell is not None
    assert "_required_globals = ['tract_agg', 'params']" in summary_cell
    assert "tract_agg = globals()['tract_agg']" in summary_cell
    assert "params = globals()['params']" in summary_cell

    export_cell = next(
        (cell for cell in code_cells if cell.startswith("# Cell 13: Export Results")),
        None,
    )
    assert export_cell is not None
    assert "_required_globals = [" in export_cell
    assert "WORK_DIR = Path(globals()['WORK_DIR'])" in export_cell
    assert "final_output = globals()['final_output']" in export_cell
    assert "n_tracts = globals()['n_tracts']" in export_cell
