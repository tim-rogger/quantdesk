# QuantDesk – Roadmap

Stand: 29.09.2026. Keine Anlageberatung.

## Fazit der Forschungsphase (abgeschlossen am 29.09.2026)

**Nach fairen Regeln hat keine Strategie SPY geschlagen, weder in USD noch in CHF.**

- Die Grid/DCA-Strategie aus dem Video hat keinen eigenen Vorteil. Sie verdient nur, wenn die Aktie ohnehin steigt.
- Parameter-Optimierung hat im Test jedes Mal versagt (Overfitting).
- Momentum auf Einzelaktien sieht gut aus, ist aber durch Survivorship-Bias aufgebläht und hat die grössten Rückgänge.
- Dual Momentum mit ETFs (Kandidat A) liegt etwa gleichauf mit 60/40, aber klar unter SPY.
- **Kandidat C (Grid 5×2 %, SMA200 + Exit)** ist der einzige mit einem kleinen eigenen Vorteil: 8.6 % p.a. gegen
  6.3 % für eine Mischung aus SPY und T-Bills mit gleichem Investitionsgrad (24 %). Er ist trotzdem durchgefallen
  (nur 3 von 7 Jahren besser, in CHF auch K1). Der Vorteil stammt vor allem aus dem Corona-Crash 2020.
- In CHF fällt C zusätzlich ab, weil 76 % in **USD-Cash** liegen, das für einen Franken-Anleger mit dem Dollarkurs
  schwankt. Cash in CHF würde das Währungsrisiko beseitigen, bringt aber viel weniger Zins. Klar besser wäre C also auch dann nicht.

**Entscheidungen:**
- **Kandidat B (breiteres Aktien-Momentum) wird nicht getestet.** Das Aktien-Universum enthält nur Firmen, die es heute
  noch gibt. Selbst ein Bestehen wäre nicht glaubwürdig, solange keine Daten mit Pleitefirmen vorliegen.
- **Kandidat C wird nicht weiter im Backtest angepasst.** Wir haben diese Daten schon zu oft angeschaut, jede weitere
  Änderung wäre Überanpassung. Der einzige faire Test für C ist die Zukunft: der **Vorwärtstest** unten.

### Ergebnis Walk-forward (7 Testjahre Okt 2019 – Sep 2026, Cash verzinst, Kosten inkl., USD)

| Strategie | Rendite p.a. | Sharpe | max DD | Ø inv. | Mix gl. Inv. | Jahre besser | K1 | K2 | K3 | K4 |
|---|---|---|---|---|---|---|---|---|---|---|
| **SPY halten (Massstab)** | 17.1 % | 0.76 | −33.7 % | 100 % | | | | | | |
| VT halten (Welt-ETF) | 14.4 % | 0.66 | −34.2 % | 100 % | | | | | | |
| 60/40 SPY/AGG | 10.5 % | 0.64 | −21.6 % | 100 % | | | | | | |
| Alle 49 Aktien gleich gewichtet* | 20.3 % | 0.83 | −34.7 % | 100 % | 17.1 % | 3/7 | ✗ | ✗ | ✗ | ✓ |
| Grid Video | 14.3 % | 0.69 | −30.2 % | 71 % | 13.0 % | 3/7 | ✗ | ✓ | ✗ | ✓ |
| Grid + SMA200 | 10.8 % | 0.68 | −22.4 % | 49 % | 9.9 % | 4/7 | ✗ | ✓ | ✗ | ✓ |
| **C: Grid + SMA200 + Exit** | 8.6 % | 1.02 | −6.1 % | 24 % | 6.3 % | 3/7 | ✓ | ✓ | ✗ | ✓ |
| Grid, je Fenster optimiert | 6.0 % | 0.48 | −12.7 % | 30 % | 7.1 % | 2/7 | ✗ | ✓ | ✗ | ✗ |
| Momentum Top 5 (Aktien)* | 29.6 % | 0.88 | −41.9 % | 100 % | 17.1 % | 2/7 | ✗ | ✗ | ✗ | ✓ |
| A: Dual Momentum klassisch | 10.1 % | 0.47 | −33.7 % | 100 % | 17.1 % | 2/7 | ✗ | ✓ | ✗ | ✗ |
| A: Dual Momentum breit | 14.5 % | 0.66 | −29.7 % | 100 % | 17.1 % | 3/7 | ✗ | ✓ | ✗ | ✗ |

\* aufgebläht durch Survivorship-Bias.

## Bestehen-Regel für Backtests (Phase 1)

Walk-forward (`python research.py --walk-forward`): 3 Jahre Training, 1 Jahr Test, um 1 Jahr verschoben; Testjahre
zu einer Kapitalkurve verbunden. Cash mit dem 3-Monats-T-Bill-Zins verzinst, Sharpe = Überrendite über diesem Zins,
Handelskosten inklusive, Auswertung in USD **und** CHF. Massstab: Kaufen & Halten SPY. Alle vier müssen gelten:

| # | Kriterium |
|---|---|
| K1 | Sharpe über alle Testjahre **mindestens 0.2 höher** als SPY |
| K2 | grösster Rückgang (max DD) **nicht schlimmer** als bei SPY |
| K3 | in **mindestens 2/3 der einzelnen Testjahre** höhere Sharpe als SPY |
| K4 | **mehr Rendite** als SPY + T-Bills mit **demselben Ø Investitionsgrad** |

| Datum | Änderung | Begründung |
|---|---|---|
| 29.09.2026 | Erste Fassung: K1 „Sharpe höher als SPY“, K2, K3 | vor dem ersten Walk-forward-Lauf |
| 29.09.2026 | **Vor dem Test von Kandidat A verschärft:** K1 +0.2, neu K4, Cash-Zinsen, Sharpe mit Zins, CHF, VT und 60/40 | Mit der ersten Fassung hätte schon 0.81 gegen 0.80 bestanden, und Strategien mit viel Cash sahen ohne Zinsvergleich zu gut aus. Das Ergebnis von A war zu diesem Zeitpunkt nicht bekannt. |

## Bestehen-Regel für den Vorwärtstest von C (Phase 3) – festgelegt am 29.09.2026, VOR dem Start

**Was getestet wird:** Kandidat C genau wie im Backtest: Grid 5 Levels × 2 %, Einstieg und Nachkäufe nur, wenn der
Vortag über dem 200-Tage-Durchschnitt schloss, sonst alles verkaufen. **1'000 $ pro Order**, auf den 49 Aktien aus
`data/universe.csv`, Budget 294'000 $. Einziger Unterschied zum Backtest: Der Bot kauft ganze Aktien
(1'000 $ ÷ Preis, gerundet, mindestens 1), der Backtest rechnet mit Bruchteilen.

**Dauer:** 6 Testmonate = 126 Handelstage ab dem ersten echten Paper-Fill (Start = Tim stellt auf `PAPER` um).

**Messung:** `python forward_test.py report`, jeden Monat. Cash verzinst mit dem T-Bill-Zins, Kosten inklusive,
USD und CHF. Vergleich mit SPY und mit der Mischung SPY + Cash mit demselben Ø Investitionsgrad wie C.

**Bestanden nur, wenn alle fünf gelten:**

| # | Kriterium |
|---|---|
| F1 | Rendite von C **höher** als die Mischung SPY + Cash mit gleichem Investitionsgrad (USD **und** CHF) |
| F2 | grösster Rückgang von C **nicht schlimmer** als bei SPY im selben Zeitraum (USD **und** CHF) |
| F3 | in **mindestens 4 von 6 Testmonaten** (je 21 Handelstage) Monatsrendite von C ≥ Mischung |
| F4 | Gesamtrendite live weicht **höchstens 2 Prozentpunkte** vom Backtest desselben Zeitraums ab |
| F5 | **keine technischen Fehler:** keine doppelt gebuchten Orders, kein zweiter Einstieg ohne Verkauf dazwischen |

F4 prüft, ob der Bot wirklich das tut, was der Backtest annimmt. Weicht er stark ab, ist entweder der Code falsch oder
der Backtest zu optimistisch (Schlupf, Fills), und die Backtest-Ergebnisse oben sind dann nicht vertrauenswürdig.

**Ehrlich vorweg:** 6 Monate sind statistisch sehr wenig. Auch ein Bestehen ist nur ein schwaches Signal, kein Beweis.
C ist im Backtest bereits durchgefallen, der Vorwärtstest ist ein Experiment. Er ist kein grünes Licht für echtes Geld.
Diese Regel wird während des Tests **nicht** geändert.

## Vorfälle während des Vorwärtstests

Hier wird jedes technische Problem mit Datum festgehalten. Die Bestehen-Regel oben bleibt unverändert.

| Datum | Vorfall | Folge | Behebung |
|---|---|---|---|
| 07.10.2026 | **Fills früherer Sitzungen nicht erkannt.** IBKR listet unter `/iserver/account/orders` nur Orders der laufenden Sitzung. Limit-Buys, die ausgeführt wurden, während der Bot aus war, galten weiter als „platziert“ (z.B. KO Level 1 @ 85.26, gefüllt am 02.10.). | Positionen und Verkäufe waren korrekt, weil sie direkt bei IBKR abgefragt werden. Im Journal fehlten aber 25 Käufe, darunter ein Level-Kauf von CVS vor dessen Verkauf am 07.10. Der Monatsreport hätte falsch gerechnet. | Der Bot fragt beim Start und bei fehlenden Orders `/iserver/account/trades` ab, archiviert jede Ausführung (`executions.jsonl`) und bucht fehlende Fills mit echtem Datum, Preis und Stückzahl nach. **20** Fills ab dem 01.10. sind exakt nachgebucht. **5** Käufe vom 29./30.09. (ELV, M, MMM, PYPL, LLY) sind bei IBKR nicht mehr abrufbar (nur 7 Tage). Sie werden aus der Position abgeleitet, zum Limitpreis, der Tag ist aus den Yahoo-Tagestiefs geschätzt und im Journal als `estimated` markiert. |
| 07.10.2026 | **WBD-Position verschwunden** (32 Stück, gekauft am 29.09. zu 30.85), ohne Verkauf durch den Bot. Yahoo liefert keine Kurse mehr. | Das Journal führte 32 WBD, IBKR 0. | **Geklärt:** Übernahme von Warner Bros. Discovery durch Paramount Skydance gegen Bargeld, Abschluss 06.10.2026, **31.01666668 $ je Aktie** (31.00 $ + Tageszulage nach dem 30.09., laut SEC-Unterlagen). Mit `forward_test.py close` als Verkauf (`corporate_action`) gebucht, System geschlossen. Tim prüft die Gutschrift (≈ 992.53 $) im Kontoauszug. |
| 29.09.–07.10.2026 | **Bot lief nur am 29.09. und am 07.10.** (Laptop-Betrieb: Gateway + Bot + täglicher Browser-Login nötig). | 30.09.–06.10.: keine Trend-Entscheide, keine neuen Einstiege, keine Trend-Verkäufe. Die GTC-Limit-Orders bei IBKR liefen weiter und wurden teils ausgeführt. C weicht in dieser Woche vom Backtest ab (relevant für F4). | Umzug auf einen Server mit IB Gateway + IBC, Tageslauf an jedem NYSE-Handelstag 10:00 und 15:30 New York (`deploy/SERVER.md`). |
| 07.10.2026 | **Fremde Bestände konnten übernommen werden.** Der Bot übernahm bestehende Positionen als Einstieg und hätte bei einem Trendbruch den ganzen Bestand verkauft, auch Aktien, die nicht von ihm stammen. Fills ohne bekannte Order wurden nachgebucht. | Bisher ohne Schaden: Alle Positionen stammen vom Bot. | Order-Register (`bot_orders.jsonl`, rückwirkend aus `quantdesk.log`): Nur Fills eigener Orders zählen. Verkauft wird höchstens `min(eigene Stück, Bestand)`. Fremde Bestände werden nie übernommen. Abweichungen melden einen Fehler und einen Push. |
| 29.09.–07.10.2026 | **Keine Handelsberechtigung** für MA, T, C, ADP, ZM (IBKR: „No trading permissions“). | Diese 5 Systeme haben nie gehandelt. | Als `not_tradable` gesperrt, zählen nicht zum Budget und nicht zum Report (Live und Backtest gleich behandelt). Budget jetzt 44 Systeme × 6'000 $ (WBD zählt bis zur Schliessung mit). |
| 08.10.2026 | **Erster Server-Lauf: 27 Symbole gesperrt, `bot_qty = inf`.** Der TWS-Adapter las bei offenen Orders `Order.filledQuantity` – bei ib_async ist das der Platzhalter `UNSET_DOUBLE` (1.8e308) – als gefüllte Menge und leitete daraus den Status „Filled“ ab. Offene GTC-Levels galten so als gefüllt. Dazu: „nan $“ in der Zusammenfassung, 10167 („verzögerte Kurse“) und ntfy 403 bei jedem Ereignis im Log, WBD ohne Kontraktdefinition (Error 200), Ops-Fehler (Dashboard-Image gepullt, `tws_password` „Permission denied“, `chown 1000` passt nicht zu Tims UID 1001, fehlende Secret-Dateien stoppen `up`). | Lief im DRY_RUN: keine Order gesendet. Plausibilitätsprüfung hat korrekt alle betroffenen Symbole gesperrt. | Adapter liest nur echte Werte (Status kommt von IBKR, nie aus der Menge). Harte Regel überall: nie nan/inf oder unplausible Mengen/Preise in `bot_qty`, Journal, `equities.json` – beim Laden verworfen, Symbol mit Grund gesperrt (`blocked`). `forward_test.py migrate` (mit `--dry-run`) bereinigt Journal und Zustand und füllt `entry_qty`, Level-Mengen und Order-Register. Symbole ohne Kontrakt werden gesperrt und nicht mehr angefragt. Hinweise einmal pro Lauf. `deploy/prepare.sh` setzt UID, Rechte und leere Platzhalter-Secrets. |

F5 („keine doppelt gebuchten Orders, kein zweiter Einstieg ohne Verkauf dazwischen“) ist durch den ersten Vorfall nicht
verletzt: Es wurde nichts doppelt gehandelt, nur unvollständig gebucht. Der Vorfall wird trotzdem beim Urteil erwähnt.

## Phasen

### Phase 0 – Aufräumen ✅
- [x] Bot auf Python/IBKR Paper/Claude umgebaut, Bugs aus dem Video behoben
- [x] Live-Test DRY_RUN + eine echte Paper-Order
- [ ] Claude-Chat mit API-Key testen (Tim)

### Phase 1 – Forschung ✅ (abgeschlossen, Fazit oben)
- [x] Backtester, faire Kennzahlen, Universum, Trendfilter, Momentum, Walk-forward, Bestehen-Regel
- [x] Kandidat A (Dual Momentum) → durchgefallen
- [x] Kandidat B → gestrichen (Survivorship-Bias)
- [x] Kandidat C → im Backtest knapp durchgefallen, geht in den Vorwärtstest

### Phase 2 – Bot für C umbauen ✅
- [x] Trendfilter im Bot, dieselbe Regel wie im Backtest (Test prüft Tag für Tag die Gleichheit)
- [x] Verkauf bei Trendbruch: offene Orders stornieren, Position per Market verkaufen, danach neuer Zyklus
- [x] Ordergrösse in $ wie im Backtest, Handelsjournal (`journal.jsonl`)
- [x] Test: Die Engine macht Tag für Tag dieselben Käufe und Verkäufe wie die Backtest-Simulation
- [x] `forward_test.py setup` (C-Systeme anlegen) und `forward_test.py report` (Monatsreport)

### Phase 3 – Vorwärtstest von C auf dem Paper-Konto (6 Monate) ⏳ läuft seit 29.09.2026
- [x] Vorbereitung im DRY_RUN, Start im PAPER-Modus am 29.09.2026
- [x] Fills früherer Sitzungen nachbuchen, Order-Register, nur eigene Aktien, `not_tradable`, WBD geschlossen
- [ ] **Server-Betrieb** (`deploy/SERVER.md`, Plan „QuantDesk – IT-Infrastruktur-Plan“): Contabo Cloud VPS 6 (EU,
      Ubuntu 24.04), Einrichtung über die Web-Konsole, **SSH nur über Tailscale** (Port 22 zu), `deploy/harden.sh`,
      Docker, IB Gateway + IBC, Dashboard, ntfy
- [ ] Backups: restic → Backblaze B2, verschlüsselt, täglich nach dem 15:30-Abgleich (30 täglich / 12 monatlich);
      Restore-Test einmal pro Quartal (`deploy/restore.sh`)
- [ ] Überwachung von aussen: Healthchecks.io (Ping nach jedem Handelslauf, Alarm wenn bis 11:00 New York keiner kam)
- [ ] Ab Server-Start: Tageslauf an jedem NYSE-Handelstag 10:00 (Handel) und 15:30 (Abgleich) New York, Push nach jedem Lauf
- [ ] Monatlich `forward_test.py report` (bzw. im Dashboard: F1–F5)
- [ ] Nach 126 Handelstagen: Urteil nach F1–F5 (Vorfälle oben beim Urteil erwähnen)

### Phase 4 – Entscheidung über echtes Geld (Tims Entscheidung)
- Nur, wenn C den Vorwärtstest besteht, und auch dann ist das nur ein schwaches Signal.
- Der Bot hat bewusst **keinen** Echtgeld-Modus. Das wäre ein eigener, bewusster Umbau mit zusätzlichen
  Grenzen (Höchstbetrag, tägliches Verlustlimit, Kill-Switch).
- Rechne ehrlich damit, dass C nicht besteht. Dann lautet die Erkenntnis: Ein breiter ETF, den man einfach hält,
  war nach fairen Regeln nicht zu schlagen.

## Bekannte Grenzen

- Survivorship-Bias: Einzelaktien-Universum enthält nur heute existierende Firmen (ETFs kaum betroffen).
- Nicht eingerechnet: Schlupf im Backtest, Steuern, Wechselkosten CHF → USD; CHF-Zins als 0 angenommen.
- Cash-Zinsen als Näherung: nachträglich auf die Kapitalkurve gerechnet, nicht in die Handelsentscheidungen.
- Journal-Tag = Tag, an dem der Bot den Fill bemerkt (UTC). Läuft der Bot über Nacht nicht, kann ein Fill einen Tag später gebucht werden.
- 10 Jahre Daten = wenige Marktphasen; ein Crash wie 2008 ist nicht enthalten.
