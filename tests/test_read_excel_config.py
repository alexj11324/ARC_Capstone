from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import scripts.read_excel_config as read_excel_config


def test_load_config_reads_major_damage_from_column_e(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: % Major Damage lives in column E on the Interface sheet."""
    df = pd.DataFrame([[pd.NA] * 5 for _ in range(20)])
    df.iloc[12, 2] = 0.35
    df.iloc[12, 3] = 0.91
    df.iloc[12, 4] = 0.12
    df.iloc[13, 2] = "0.11 - 0.34"
    df.iloc[13, 3] = "0.92 - 0.99"
    df.iloc[13, 4] = "0.22 - 0.33"

    config_path = tmp_path / "interface.xlsx"
    config_path.touch()

    monkeypatch.setattr(read_excel_config.pd, "read_excel", lambda *args, **kwargs: df)

    params = read_excel_config.load_config_from_excel(config_path)

    assert params["TRACT_SEVERITY"]["high"]["pct_major_damage"] == pytest.approx(0.12)
    assert params["TRACT_SEVERITY"]["medium"]["pct_major_damage"] == pytest.approx(0.22)
    assert params["DAMAGE_SEVERITY"]["high"]["pct_major_damage"] == pytest.approx(0.12)
    assert params["DAMAGE_SEVERITY"]["medium"]["pct_major_damage"] == pytest.approx(0.22)


def test_load_config_keeps_new_runtime_defaults_when_excel_omits_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Notebook runtime defaults should still exist after Excel overlay."""
    df = pd.DataFrame([[pd.NA] * 5 for _ in range(20)])
    config_path = tmp_path / "interface.xlsx"
    config_path.touch()

    monkeypatch.setattr(read_excel_config.pd, "read_excel", lambda *args, **kwargs: df)

    params = read_excel_config.load_config_from_excel(config_path)

    assert params["geography"] == "census tract"
    assert params["PERCENT_IMPACT"] == {
        "high": pytest.approx(5.0),
        "medium": pytest.approx(2.5),
        "low": pytest.approx(0.0),
    }
