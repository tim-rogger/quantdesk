"""Mehrfachtest-Korrektur (DSR) und Register der getesteten Strategien."""
import math
import shutil
import statistics

import pytest

from quantdesk import multitest as mt
from quantdesk import trials

N = statistics.NormalDist()


@pytest.mark.parametrize("n,exact", [(10, 1.5388), (100, 2.5076)])
def test_expected_max_matches_order_statistics(n, exact):
    """Erwartetes Maximum von N Standardnormalen (Tabellenwerte) – die Näherung liegt nahe daran."""
    assert mt.expected_max_sharpe(n, 1.0) == pytest.approx(exact, abs=0.05)
    assert mt.expected_max_sharpe(1, 1.0) == 0.0
    with pytest.raises(ValueError):
        mt.expected_max_sharpe(0, 1.0)


def test_psr_normal_case():
    sr, t = 0.05, 2000
    assert mt.psr(sr, 0.0, t) == pytest.approx(N.cdf(sr * math.sqrt(t - 1) / math.sqrt(1 + sr * sr / 2)))
    assert mt.psr(sr, sr, t) == pytest.approx(0.5)
    assert mt.psr(sr, 0.0, t, skew=-1.0, kurt=10) < mt.psr(sr, 0.0, t)  # Linksschiefe & dicke Ränder: weniger sicher


def returns(n=5000, mean=0.0004, sd=0.01):
    """Deterministische Renditen mit exakt bekanntem Mittel/Streuung (symmetrisch)."""
    return [mean + (sd if i % 2 else -sd) for i in range(n)]


def test_moments_and_deflated_sharpe():
    r = returns()
    mean, sd, skew, kurt = mt.moments(r)
    assert (mean, sd, skew) == (pytest.approx(0.0004), pytest.approx(0.01), pytest.approx(0.0, abs=1e-9))
    one = mt.deflated_sharpe(r, 1)
    many = mt.deflated_sharpe(r, 150)
    assert one.sharpe == pytest.approx(0.04 * math.sqrt(252))
    assert one.dsr == pytest.approx(one.psr) and one.sharpe_threshold == 0.0
    assert many.dsr < one.dsr and many.sharpe_threshold > 0.5
    with pytest.raises(ValueError):
        mt.moments([1.0, 1.0, 1.0])


def test_required_sharpe_is_consistent():
    req = mt.required_sharpe(12, 20)
    t = 20 * 252
    sr0 = mt.expected_max_sharpe(12, 1 / (t - 1))
    assert mt.psr(req / math.sqrt(252), sr0, t) == pytest.approx(0.95, abs=1e-3)
    assert mt.required_sharpe(1, 20) < req < mt.required_sharpe(150, 20)
    assert mt.required_sharpe(12, 30) < req  # längerer Test -> weniger Glück möglich


# ---------------------------------------------------------------- Register
def test_registry_phase1_counts():
    t = trials.read()
    assert [x.nr for x in t] == list(range(1, len(t) + 1))
    assert t[4].name.startswith("**C:") and t[0].new_trials == 144
    assert trials.counts(t[:10]) == (149, 9)  # Phase 1: 149 Kombinationen, 9 Strategievarianten


def test_registry_append_and_result(tmp_path):
    path = tmp_path / "registry.md"
    shutil.copy(trials.REGISTRY, path)
    before = trials.counts(trials.read(str(path)))
    new = trials.append(trials.Trial(0, "2026-10-09", "2", "D1 | Test", "p", "ETF", 1, "angemeldet", "–"), str(path))
    rows = trials.read(str(path))
    assert new.nr == len(rows) and rows[-1].name == "D1 / Test"  # '|' bricht die Tabelle nicht
    assert trials.counts(rows) == (before[0] + 1, before[1] + 1)
    trials.set_result(new.nr, "getestet", "durchgefallen", str(path))
    assert trials.read(str(path))[-1].status == "getestet"
    with pytest.raises(KeyError):
        trials.set_result(999, "x", "y", str(path))


def test_registration_gate(tmp_path):
    root = tmp_path / "anmeldungen"
    root.mkdir()
    assert trials.registration_status("D", str(root)) == "FEHLT"
    (root / "D.md").write_text("# D\n\n**Status:** ENTWURF – wartet auf Tim\n", encoding="utf-8")
    with pytest.raises(PermissionError, match="ENTWURF"):
        trials.require_confirmed("D", str(root))
    (root / "D.md").write_text("# D\n\n**Status:** BESTÄTIGT von Tim am 10.10.2026\n", encoding="utf-8")
    trials.require_confirmed("D", str(root))
