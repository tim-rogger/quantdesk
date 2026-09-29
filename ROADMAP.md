# QuantDesk – Roadmap

Stand: 29.09.2026. Keine Anlageberatung.

## Was wir wissen

- Der Bot funktioniert technisch: IBKR Paper, echte Paper-Order ausgeführt, DRY_RUN, STOP ALL, CI grün.
- Die Grid/DCA-Strategie aus dem Video hat **keinen eigenen Vorteil**.
- **Walk-forward (7 Testjahre Okt 2019 – Sep 2026, Cash verzinst, Kosten inkl.): keine Strategie besteht,
  weder in USD noch in CHF.** (`python research.py --walk-forward`)

  | Strategie (USD) | Rendite p.a. | Sharpe | max DD | Ø inv. | Mix gl. Inv. | Jahre besser | K1 | K2 | K3 | K4 |
  |---|---|---|---|---|---|---|---|---|---|---|
  | **SPY halten (Massstab)** | 17.1 % | 0.76 | −33.7 % | 100 % | | | | | | |
  | VT halten (Welt-ETF) | 14.4 % | 0.66 | −34.2 % | 100 % | | | | | | |
  | 60/40 SPY/AGG | 10.5 % | 0.64 | −21.6 % | 100 % | | | | | | |
  | Alle 49 Aktien gleich gewichtet* | 20.3 % | 0.83 | −34.7 % | 100 % | 17.1 % | 3/7 | ✗ | ✗ | ✗ | ✓ |
  | Grid Video | 14.3 % | 0.69 | −30.2 % | 71 % | 13.0 % | 3/7 | ✗ | ✓ | ✗ | ✓ |
  | Grid + SMA200 | 10.8 % | 0.68 | −22.4 % | 49 % | 9.9 % | 4/7 | ✗ | ✓ | ✗ | ✓ |
  | Grid + SMA200 + Exit | 8.6 % | 1.02 | −6.1 % | 24 % | 6.3 % | 3/7 | ✓ | ✓ | ✗ | ✓ |
  | Grid, je Fenster optimiert | 6.0 % | 0.48 | −12.7 % | 30 % | 7.1 % | 2/7 | ✗ | ✓ | ✗ | ✗ |
  | Momentum Top 5 (Aktien)* | 29.6 % | 0.88 | −41.9 % | 100 % | 17.1 % | 2/7 | ✗ | ✗ | ✗ | ✓ |
  | **A: Dual Momentum klassisch** (SPY/EFA → AGG) | 10.1 % | 0.47 | −33.7 % | 100 % | 17.1 % | 2/7 | ✗ | ✓ | ✗ | ✗ |
  | **A: Dual Momentum breit** (SPY/EFA/EEM/TLT/GLD → AGG) | 14.5 % | 0.66 | −29.7 % | 100 % | 17.1 % | 3/7 | ✗ | ✓ | ✗ | ✗ |

  \* aufgebläht durch Survivorship-Bias (nur heute existierende Firmen).

  - **Kandidat A (Dual Momentum) ist durchgefallen.** Die breite Variante liegt fast gleichauf mit 60/40
    (Sharpe +0.02, Rendite +4.1 %), aber klar unter SPY. Die klassische Variante lag in 2019/20 und 2022/23
    mit Fehlsignalen falsch (schnelle Marktwenden).
  - **Grid + SMA200 + Exit** erfüllt in USD K1, K2 und K4 und schlägt damit auch die Mischung aus 24 % SPY und
    76 % T-Bills. Es scheitert aber an K3 (nur 3 von 7 Jahren besser), und in **CHF fällt es auch bei K1 durch**
    (Sharpe 0.66 gegen 0.73). Der Vorteil stammt grösstenteils aus dem Corona-Crash 2020.
- **Der Bot bleibt im `DRY_RUN`**, bis eine Strategie die Bestehen-Regel erfüllt.

## Bestehen-Regel

Geprüft mit dem **Walk-forward-Test** (`python research.py --walk-forward`): 3 Jahre Training, 1 Jahr Test,
dann um 1 Jahr verschieben. Nur die Testjahre zählen, aneinandergehängt zu einer Kapitalkurve.
Nicht investiertes Geld bekommt den 3-Monats-T-Bill-Zins (`^IRX`), die Sharpe misst die Überrendite über diesem Zins.
Handelskosten (1 $ pro Order) sind drin, ETF-Gebühren stecken in den Kursen. Ausgewertet in **USD und CHF**
(CHF: Kurs USD/CHF, CHF-Zins vereinfacht = 0). Massstab: **Kaufen & Halten SPY**. Eine Strategie besteht nur,
wenn **alle vier** gelten, und zwar in USD **und** in CHF:

| # | Kriterium |
|---|---|
| K1 | Sharpe über alle Testjahre **mindestens 0.2 höher** als SPY |
| K2 | grösster Rückgang (max DD) über alle Testjahre **nicht schlimmer** als bei SPY |
| K3 | in **mindestens 2/3 der einzelnen Testjahre** höhere Sharpe als SPY |
| K4 | **mehr Rendite** als eine Mischung aus SPY und T-Bills mit **demselben Ø Investitionsgrad** |

Zusätzlich angezeigt (nicht Teil der Regel): Welt-ETF VT und 60/40 SPY/AGG. Für Strategien, die zeitweise
Anleihen halten (Dual Momentum), ist 60/40 der fairere Vergleich.

### Änderungsprotokoll der Regel

| Datum | Änderung | Begründung |
|---|---|---|
| 29.09.2026 | Erste Fassung: K1 „Sharpe höher als SPY“, K2, K3 | vor dem ersten Walk-forward-Lauf |
| 29.09.2026 | **Vor dem Test von Kandidat A verschärft:** K1 auf „mindestens +0.2“, neu K4 (gleicher Investitionsgrad), Cash-Zinsen, Sharpe mit Zins, Auswertung zusätzlich in CHF, VT und 60/40 als Vergleich | Mit der ersten Fassung hätte schon 0.81 gegen 0.80 bestanden, und Strategien mit viel Cash sahen ohne Zinsvergleich zu gut aus. Die Änderung kam vor dem Lauf von Kandidat A, dessen Ergebnis war also nicht bekannt. |

Regel nie nachträglich lockern, um einen Kandidaten durchzubringen.

## Phasen

### Phase 0 – Aufräumen ✅
- [x] Bot auf Python/IBKR Paper/Claude umgebaut, Bugs aus dem Video behoben
- [x] Live-Test DRY_RUN + eine echte Paper-Order
- [x] Backtester, faire Kennzahlen, 50-Aktien-Universum, Trendfilter, Momentum-Rotation
- [ ] Claude-Chat mit API-Key testen (Tim)

### Phase 1 – Forschung (nur am Laptop)
- [x] Bestehen-Regel festgelegt und vor Kandidat A verschärft (oben)
- [x] Walk-forward-Test mit Cash-Zinsen, CHF, VT und 60/40 (`research.py --walk-forward`)
- [x] Kandidat A: **ETF-Dual-Momentum**, klassisch und breit → **durchgefallen**
- [ ] Kandidat B: **breiteres Aktien-Momentum**: Top 10–20 statt 5, nur investieren, wenn SPY über SMA200.
      Achtung: Einzelaktien-Universum hat Survivorship-Bias, ein Bestehen wäre mit Vorsicht zu geniessen
- [ ] Kandidat C: Grid + Trendfilter + Exit, bisher der Kandidat, der am nächsten dran war (K3 und CHF-K1 fehlen)
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

- Survivorship-Bias: Einzelaktien-Universum enthält nur heute existierende Firmen (ETFs sind davon kaum betroffen).
- Nicht eingerechnet: Schlupf, Steuern, Wechselkosten beim Umtausch CHF → USD; CHF-Zins als 0 angenommen.
- Cash-Zinsen als Näherung: nachträglich auf die Kapitalkurve gerechnet, nicht in die Handelsentscheidungen.
- 10 Jahre Daten = wenige Marktphasen; ein Crash wie 2008 ist nicht enthalten.
