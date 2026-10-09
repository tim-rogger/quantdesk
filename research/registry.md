# Register aller getesteten Strategien

Jede Strategie, die je gegen historische Daten geprüft wurde, steht hier, auch verworfene Zwischenstände.
Grund: Bei genügend Versuchen besteht irgendeine Variante die Kriterien rein zufällig. Die Mehrfachtest-Korrektur
(Deflated Sharpe Ratio, `quantdesk/multitest.py`) braucht deshalb die Anzahl Versuche **N**. Sie wird aus dieser
Tabelle gezählt (`quantdesk/trials.py`).

- **Versuche neu** = Anzahl neu geprüfter Varianten in dieser Zeile. Eine Parametersuche über 144 Kombinationen
  zählt 144. Varianten, die schon in einer früheren Suche enthalten waren, zählen 0.
- **N streng** = Summe der Spalte „Versuche neu“ (jede je geprüfte Kombination).
- **N effektiv** (gilt seit 09.10.2026, Tims Festlegung in `research/anmeldungen/D.md`) = **laufender Zähler** aller
  Varianten, die je auf einem ETF-Datenstand angemeldet oder gerechnet wurden (Phase 2, Status ≠ `nicht getestet`).
  Start N = 3 (D1–D3). Jede weitere Variante erhöht N dauerhaft, der Zähler sinkt nie. Die Phase-1-Versuche liefen
  auf einem anderen Universum und Zeitraum und zählen hier nicht. Das ist die Zählweise für K5.
- Jeder neue Lauf hängt eine Zeile an bzw. trägt sein Ergebnis ein. Zeilen werden nie gelöscht.
- Kandidaten werden **vor** dem Lauf angemeldet (`research/anmeldungen/`). Vergleichsportfolios (SPY, 60/40,
  gleich gewichtet, Risikoparität) sind keine Versuche und stehen nicht hier.

## Register

| Nr | Datum | Phase | Strategie | Parameter | Daten | Versuche neu | Status | Ergebnis |
|---|---|---|---|---|---|---|---|---|
| 1 | 2026-09-29 | 1 | Grid/DCA-Parametersuche, Einzelaktien (`backtest.py --sweep`) | 3/5/10 Levels × 2/5 % × TP –/5/10/20 % × SL –/10 % × Trend –/SMA200/SMA200+Exit | 49 Aktien, Survivorship-Bias | 144 | getestet | kein eigener Vorteil gegenüber Kaufen & Halten |
| 2 | 2026-09-29 | 1 | Kaufen & Halten, 49 Aktien gleich gewichtet | monatlich nicht rebalanciert | 49 Aktien, Survivorship-Bias, WF 2019–2026 | 1 | getestet | durchgefallen (K1 ✗ K2 ✗ K3 ✗ K4 ✓), Sharpe 0.83 |
| 3 | 2026-09-29 | 1 | Grid Video | 5 Levels × 2 % | wie 2 | 0 | getestet | durchgefallen (K1 ✗ K3 ✗), Sharpe 0.69 |
| 4 | 2026-09-29 | 1 | Grid + SMA200 | 5 × 2 %, nur über SMA200 kaufen | wie 2 | 0 | getestet | durchgefallen (K1 ✗ K3 ✗), Sharpe 0.68 |
| 5 | 2026-09-29 | 1 | **C: Grid + SMA200 + Exit** | 5 × 2 %, SMA200, Verkauf bei Trendbruch, 1000 $/Order | wie 2 | 0 | getestet | durchgefallen (K3 ✗, CHF auch K1 ✗), Sharpe 1.02 → Vorwärtstest seit 29.09.2026 |
| 6 | 2026-09-29 | 1 | Grid, je Trainingsfenster optimiert | Auswahl nach Sharpe aus den 144 Kombinationen von Nr. 1, 3 J. Training | wie 2 | 1 | getestet | durchgefallen, Sharpe 0.48 (Overfitting) |
| 7 | 2026-09-29 | 1 | Momentum Top 5 (Aktien) | 63-Tage-Rendite, monatlich | wie 2 | 1 | getestet | durchgefallen, Sharpe 0.88 (Survivorship-Bias) |
| 8 | 2026-09-29 | 1 | A: Dual Momentum klassisch | SPY/EFA, sonst AGG, Hürde BIL, 12 Monate | ETFs, WF 2019–2026 | 1 | getestet | durchgefallen, Sharpe 0.47 |
| 9 | 2026-09-29 | 1 | A: Dual Momentum breit | SPY/EFA/EEM/TLT/GLD, sonst AGG, Hürde BIL | ETFs, WF 2019–2026 | 1 | getestet | durchgefallen, Sharpe 0.66 |
| 10 | 2026-09-29 | 1 | B: breiteres Aktien-Momentum | – | – | 0 | nicht getestet | gestrichen (Survivorship-Bias) |
| 11 | 2026-10-09 | 2 | D1: Trend lang | je Klasse 1/9, investiert wenn 12-M-Rendite > T-Bill, monatlich | ETF-Universum (9 Klassen), snapshot-2026-10-09, Fassungen A/B/C | 1 | angemeldet | wartet auf Tims Bestätigung der Anmeldung |
| 12 | 2026-10-09 | 2 | D2: Trend kurz+lang (Barbell) | je Klasse 1/9 × (Signal 12 M + Signal 2 M)/2, monatlich | ETF-Universum (9 Klassen), snapshot-2026-10-09, Fassungen A/B/C | 1 | angemeldet | wartet auf Tims Bestätigung der Anmeldung |
| 13 | 2026-10-09 | 2 | D3: Trend kurz+lang, beste 3 | Ø Überrendite 2 M und 12 M, beste 3 mit Punktzahl > 0 je 1/3, monatlich | ETF-Universum (9 Klassen), snapshot-2026-10-09, Fassungen A/B/C | 1 | angemeldet | wartet auf Tims Bestätigung der Anmeldung |
