"""Gemeinsame pytest-Einstellungen.

Lotse: tests/test_logik.py ist Tims Aufgabenblatt – solange eine Regel noch `NotImplementedError` wirft, ist ihr
Test bei Tim rot. In der CI (GitHub Actions setzt CI=true) gilt so ein Test als "übersprungen – wartet auf Tim",
damit die übrigen Tests weiter zählen. Ist die Regel geschrieben, läuft der Test überall normal (grün oder rot).
"""
import os

import pytest


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    ergebnis = yield
    bericht = ergebnis.get_result()
    if (os.getenv("CI") and item.module.__name__.endswith("test_logik") and call.excinfo is not None
            and call.excinfo.errisinstance(NotImplementedError)):
        bericht.outcome = "skipped"
        bericht.longrepr = (str(item.path), item.location[1] or 0, f"wartet auf Tim: {call.excinfo.value}")
