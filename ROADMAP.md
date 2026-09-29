# QuantDesk – Roadmap

Stand: 29.09.2026. Keine Anlageberatung.

## Was wir wissen

- Der Bot funktioniert technisch: IBKR Paper, echte Paper-Order ausgeführt, DRY_RUN, STOP ALL, CI grün.
- Die Grid/DCA-Strategie aus dem Video hat **keinen eigenen Vorteil**. Im Testzeitraum 2021–2026 war auf
  49 Aktien nichts klar besser als SPY einfach zu halten (`python research.py`).
  - Grid + 200-Tage-Trendfilter: gleiche Sharpe wie SPY, kleinerer Rückgang, weniger Rendite.
  - Momentum Top 5: im Training top, im Test am schlechtesten (−43.5 % Rückgang).
  - Das im Training optimierte Grid ist im Test eingebrochen (Overfitting).
- **Walk-forward (7 Testjahre 2019–2026): keine Strategie besteht.**

  | Strategie | Rendite p.a. | Sharpe | max DD | Jahre besser als SPY | K1 | K2 | K3 |
  |---|---|---|---|---|---|---|---|
  | SPY halten (Massstab) | 17.1 % | 0.90 | −33.7 % | – | | | |
  | Alle gleich gewichtet halten | 20.3 % | 0.96 | −34.7 % | 2/7 | ✓ | ✗ | ✗ |
  | Grid Video | 13.6 % | 0.80 | −30.3 % | 3/7 | ✗ | ✓ | ✗ |
  | Grid + SMA200 | 9.6 % | 0.80 | −22.5 % | 4/7 | ✗ | ✓ | ✗ |
  | Grid + SMA200 + Exit | 6.6 % | 1.12 | −7.1 % | 3/7 | ✓ | ✓ | ✗ |
  | Grid, je Fenster optimiert | 4.3 % | 0.63 | −13.9 % | 2/7 | ✗ | ✓ | ✗ |
  | Momentum Top 5 | 29.6 % | 0.96 | −41.9 % | 2/7 | ✓ | ✗ | ✗ |

  Am nächsten dran: Grid + SMA200 + Exit (bessere Sharpe, viel kleinerer Rückgang). Aber nur in 3 von 7 Jahren
  besser als SPY, ein grosser Teil des Vorteils stammt aus dem Corona-Crash 2020. Dazu nur 25 % investiert und 6.6 % p.a.
- **Der Bot bleibt im `DRY_RUN`**, bis eine Strategie die Bestehen-Regel unten erfüllt.

## Bestehen-Regel (vorher festgelegt – nicht nachträglich ändern)

Geprüft mit dem **Walk-forward-Test** (`python research.py --walk-forward`): 3 Jahre Training, 1 Jahr Test,
dann um 1 Jahr verschieben. Nur die Testjahre zählen, aneinandergehängt zu einer Kapitalkurve.
Vergleich: **Kaufen & Halten SPY** über dieselben Testjahre. Eine Strategie besteht nur, wenn **alle drei** gelten:

| # | Kriterium |
|---|---|
| K1 | Sharpe über alle Testjahre **höher** als SPY |
| K2 | grösster Rückgang (max DD) über alle Testjahre **nicht schlimmer** als bei SPY |
| K3 | in **mindestens 2/3 der einzelnen Testjahre** höhere Sharpe als SPY |

Warum so streng: Mit 7 Testjahren kann eine Strategie leicht durch Zufall ein, zwei gute Jahre haben.
Und wer die Regel ändert, nachdem er die Ergebnisse gesehen hat, betrügt sich selbst.

## Phasen

### Phase 0 – Aufräumen ✅
- [x] Bot auf Python/IBKR Paper/Claude umgebaut, Bugs aus dem Video behoben
- [x] Live-Test DRY_RUN + eine echte Paper-Order
- [x] Backtester, faire Kennzahlen, 50-Aktien-Universum, Trendfilter, Momentum-Rotation
- [ ] Claude-Chat mit API-Key testen (Tim)

### Phase 1 – Forschung (nur am Laptop)
- [x] Bestehen-Regel festgelegt (oben)
- [x] Walk-forward-Test (`research.py --walk-forward`)
- [ ] Kandidat A: **ETF-Momentum** („Dual Momentum“): US-Aktien, Welt ohne USA, Schwellenländer,
      Anleihen, Gold. Monatlich das stärkste mit positiver Rendite halten, sonst Anleihen/Cash
- [ ] Kandidat B: **breiteres Aktien-Momentum**: Top 10–20 statt 5, nur investieren, wenn SPY über SMA200
- [ ] Kandidat C: Grid + Trendfilter (bleibt als Vergleich)
- [ ] Nur diese wenigen, vorher festgelegten Varianten testen, keine Parameter-Jagd

### Phase 2 – Bot umbauen (nur wenn ein Kandidat Phase 1 besteht)
- [ ] Strategie in die Engine, mit derselben Logik wie im Backtest, mit Tests abgesichert
- [ ] Verkaufsregeln, monatliches Rebalancing, Gewinn/Verlust-Spalte in der GUI
- [ ] Zuerst DRY_RUN

### Phase 3 – Paper-Forward-Test (3–6 Monate)
- [ ] Bot läuft auf dem Paper-Konto, monatlicher Vergleich gegen SPY
- [ ] Weicht er stark vom Backtest ab → Backtest war zu optimistisch → zurück zu Phase 1

### Phase 4 – Entscheidung über Echtgeld (Tims Entscheidung)
- Der Bot hat bewusst **keinen** Echtgeld-Modus. Das wäre ein eigener, bewusster Umbau mit zusätzlichen
  Grenzen (Höchstbetrag, tägliches Verlustlimit, Kill-Switch).
- Realistisch: Findet Phase 1 nichts Besseres als einen breiten ETF, ist genau das die Erkenntnis.

## Bekannte Grenzen der Backtests

- Survivorship-Bias: Einzelaktien-Universum enthält nur heute existierende Firmen.
- Nicht eingerechnet: Schlupf, Steuern, Wechselkurs (Konto in CHF).
- 10 Jahre Daten = wenige Marktphasen; ein Crash wie 2008 ist nicht enthalten.
