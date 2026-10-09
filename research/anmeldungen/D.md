# Anmeldung Kandidat D – Trendfolge über mehrere Anlageklassen

**Status:** BESTÄTIGT von Tim am 09.10.2026 (im Chat, eingetragen von Claude/Cowork).
**Zählweise N:** `effektiv`, als **laufender Zähler** – siehe Abschnitt „Zählweise N“ unten.

Angemeldet am 09.10.2026, **bevor** irgendein Ergebnis von D auf echten Daten berechnet wurde. Code:
`quantdesk/etf_strategies.py` (Regeln), `quantdesk/allocation.py` (Simulator), `quantdesk/phase2.py` (Kennzahlen,
Urteil), `quantdesk/phase2_run.py` (Ablauf). Getestet ist er bisher nur mit künstlichen Daten (`tests/test_phase2.py`).
Nach dem Lauf wird an Parametern und Regeln **nichts** geändert. Jede Änderung wäre ein neuer Versuch mit eigener
Registerzeile und eigener Anmeldung.

Registerzeilen: Nr. 11–13 (`research/registry.md`).

## Warum diese Familie

Trendfolge ist die einzige Familie mit Belegen über ein Jahrhundert und viele Märkte (Hurst/Ooi/Pedersen;
Moskowitz/Ooi/Pedersen 2012). Sie diversifiziert weg vom Aktienrisiko, und genau das belohnt K2. Nach neuerer
Arbeit (arXiv 2510.23150) bringt die Kombination aus kurzem und langem Fenster fast die ganze Leistung, die mittlere
Zeitebene kaum etwas. Deshalb ein „Barbell“ aus 2 und 12 Monaten.

## Regeln (fest)

| | |
|---|---|
| Universum | 9 Anlageklassen: SPY, EFA, EEM, IEF, TLT, LQD, GLD, DBC, VNQ (`research/DATEN.md`) |
| Cash | T-Bill-Zins (`^IRX`), täglich gutgeschrieben |
| Signal | „Trend positiv“ = Gesamtrendite über das Fenster **höher als der T-Bill-Zins** im selben Zeitraum |
| Fenster | lang **252** Handelstage (12 Monate), kurz **42** Handelstage (2 Monate) |
| Entscheid | am letzten Handelstag jedes Monats, mit dem Schlusskurs dieses Tages |
| Ausführung | am **nächsten** Handelstag zum Schlusskurs (1 Tag Verzögerung), jeden Monat exakt auf die Zielgewichte |
| Kosten | 1 $ Kommission je Order + 5 Basispunkte Schlupf auf den gehandelten Betrag |
| Kapital | 100'000 $, long-only, **kein Hebel**, Summe der Gewichte ≤ 100 % |
| Vorlauf | eine Klasse ist erst handelbar, wenn sie ≥ 253 Kurse Historie hat |

**Varianten (genau drei, vorher festgelegt):**

| | Regel |
|---|---|
| D1 – nur lang | je Klasse 1/9 investiert, wenn das 12-Monats-Signal positiv ist, sonst dieser Anteil in Cash |
| D2 – kurz + lang | je Klasse 1/9 × (Signal 12 M + Signal 2 M) / 2, also 0, halb oder ganz investiert |
| D3 – kurz + lang, beste 3 | Punktzahl = Ø der Überrendite über T-Bill nach 2 M und nach 12 M; die **3** besten mit Punktzahl > 0 je 1/3, Rest Cash |

Wöchentliche Entscheidung wird **nicht** getestet (wäre eine weitere Variante).

## Zeiträume und Fassungen

| Auswertung | Daten | Zeitraum | entscheidet? |
|---|---|---|---|
| **A** | verkettet | 02.01.2004 – Ende Datenstand (≈ 22.8 Jahre) | ja |
| **B** | nur echte ETF-Daten | ab dem ersten Tag mit allen 9 ETFs + 1 Jahr Vorlauf (≈ 07.02.2007, ≈ 19.6 Jahre) | **ja, bei Abweichung gilt B** |
| **C** | verkettet, Ersatzreihen korrigiert | wie A | nein, zeigt Richtung/Grösse des Fehlers |
| Neben | verkettet, wachsendes Universum | ab 02.01.1996 | nein, zeigt nur 2000–2002 |

Datenstand: `snapshot-2026-10-09`. Testjahre = aufeinanderfolgende Blöcke von 252 Handelstagen ab Start, ein Rest
zählt nur mit ≥ 63 Tagen. D hat keine angepassten Parameter. Deshalb gibt es kein Trainingsfenster: Die Strategie
läuft durchgehend, und die Testjahre sind Abschnitte dieser einen Kurve.

## Vergleichsportfolios (keine Kandidaten, gleiche Kosten und Ausführung)

- **SPY halten** – Massstab für alle Kriterien
- **60/40 SPY/IEF**, monatlich zurückgesetzt (Phase 1 nahm AGG, AGG gehört nicht zum Universum)
- **G – Risikoparität**: Gewichte ∝ 1 / Schwankung der letzten 126 Tage, voll investiert, ohne Hebel. Zeigt, ob D mehr
  kann als Diversifikation.
- **Alle 9 Klassen gleich gewichtet**, monatlich: Diversifikation ohne jeden Trend

## Bestehen-Regel (gilt für D1, D2, D3 einzeln; Phase-2-Regel, siehe ROADMAP.md)

**Sicht 1 – risikobereinigt** (alle fünf, in **USD und CHF**, in Fassung A und B; bei Abweichung gilt B):

| # | Kriterium |
|---|---|
| K1 | Sharpe über den ganzen Zeitraum **≥ SPY + 0.2** |
| K2 | max Drawdown **nicht schlimmer** als SPY |
| K3 | in **≥ 2/3 der Testjahre** höhere Sharpe als SPY |
| K4 | mehr Rendite p.a. als **SPY + T-Bill mit demselben Ø Investitionsgrad** |
| K5 | **Deflated Sharpe Ratio ≥ 0.95** (Überrendite über T-Bill, täglich), N = Versuche laut Register inkl. D1–D3 |

Sharpe = Überrendite über dem T-Bill (USD) bzw. über dem CHF-Zins (CHF, FRED). CHF: Werte mit USD/CHF (FRED) umgerechnet.

**Sicht 2 – risiko-normiert** (nur USD, als **Kennzahl**, ohne Hebel im Test oder im Bot): Die Strategie wird
rechnerisch so skaliert, dass ihre Schwankung der von SPY im selben Zeitraum entspricht. Finanziert wird zum T-Bill
+ 1.5 % p.a. Ausgewiesen werden der nötige Hebel, die Rendite p.a., der max DD, der schlimmste Monat und die
Finanzierungskosten. R1: Rendite p.a. > SPY. R2: max DD nicht schlimmer als SPY. **Hinweis:** Der Hebel ist mit
der Schwankung des ganzen Zeitraums gerechnet, die man erst am Ende kennt. Er ist deshalb nicht umsetzbar und nur
ein Vergleich. Über Hebel entscheidet Tim separat und frühestens nach einem Vorwärtstest.

## Offene Entscheidung für Tim: Zählweise N (für K5)

| Zählweise | N | nötige Sharpe für DSR ≥ 0.95, Fassung A (22.8 J.) | Fassung B (19.6 J.) |
|---|---|---|---|
| **streng**: jede je geprüfte Kombination, auch die 144 Grid-Varianten aus Phase 1 | 152 | **0.90** | **0.98** |
| **effektiv**: jede Strategievariante einmal, eine Parametersuche als eine | 12 | **0.69** | **0.75** |
| (zum Vergleich: ohne Korrektur) | 1 | 0.34 | 0.37 |

Berechnet mit `quantdesk.multitest.required_sharpe`, normalverteilt. Schiefe und dicke Ränder ändern das hier kaum.
Die 144 Grid-Varianten sind fast gleich und liefen auf einem anderen Universum. Sie als 144 unabhängige Versuche zu
zählen, ist sehr streng: D müsste dann über 20 Jahre eine Sharpe von fast 1 schaffen, mehr als Trendfolge in der
Literatur meist erreicht. **Empfehlung: `effektiv`**. Dann ist meist K1 (SPY + 0.2) die härtere Hürde, K5 schützt
aber gegen weitere Varianten.

## Ergänzung bei der Bestätigung (09.10.2026, vor jedem Lauf auf echten Daten)

Beides wurde festgelegt, **bevor** ein Ergebnis von D auf echten Daten berechnet wurde.

**1. Zählweise N = `effektiv`, als laufender Zähler.**
Begründung: Die Deflated Sharpe Ratio korrigiert die Auswahl über mehrere Versuche **auf denselben Daten**.
Die 144 Grid-Varianten aus Phase 1 liefen auf einem anderen Universum (49 Aktien) und einem anderen Zeitraum
(2019–2026) und sind untereinander fast identisch. Sie als unabhängige Versuche gegen den ETF-Datenstand zu
zählen, wäre nicht streng, sondern sachlich falsch.
**Bedingung:** N ist ein laufender Zähler über alle Varianten, die je auf einem ETF-Datenstand gerechnet wurden.
Start: N = 3 (D1, D2, D3). Jede weitere Variante – bei E, F oder einem zweiten Anlauf von D – erhöht N dauerhaft
und damit die Hürde für K5. Der Zähler sinkt nie. Der jeweils gültige Wert steht im Register und in jedem Bericht.

**2. Zusätzliche Kennzahl: Mischungen aus SPY und D (kein Bestehens-Kriterium).**
Die Kriterien K1–K5 fragen „ist D besser als SPY?". Für ein reales Depot ist die nützlichere Frage:
„wird ein SPY-Depot besser, wenn D dazukommt?". Eine Strategie mit geringerer Sharpe als SPY kann ein
SPY-Depot verbessern, wenn sie wenig mit Aktien gemeinsam läuft.

Auszuweisen, je Variante und je Fassung (A, B), in USD **und** CHF, mit denselben Kosten:

| Mischung | Kennzahlen |
|---|---|
| 100 % SPY (Referenz) | Rendite p.a., Sharpe, max DD |
| 90 % SPY + 10 % D | dieselben, plus Differenz zur Referenz |
| 80 / 20 | " |
| 70 / 30 | " |
| 50 / 50 | " |

Monatlich auf die Zielgewichte zurückgesetzt, Kosten wie bei D. Zusätzlich: Korrelation der Monatsrenditen von
D zu SPY über den ganzen Zeitraum. **Das ist eine Kennzahl zum Anschauen, kein Kriterium.** Das Urteil über D
fällt allein nach K1–K5. Eine gute Mischung ist kein Bestehen und darf im Fazit nicht als solches dargestellt werden.

## Erwartetes Verhalten (vorher aufgeschrieben)

- **Rückgänge:** deutlich kleiner als SPY (2008: SPY etwa −55 %, D eher −15 bis −25 %). K2 wahrscheinlich erfüllt.
- **Rendite:** eher **unter** SPY, weil in Bullenjahren oft nur ein Teil investiert ist und Anleihen/Gold mitlaufen.
  K4 ist unsicher.
- **Sharpe:** in der Literatur für solche Portfolios etwa 0.5–0.8. **K1 (SPY + 0.2) ist die grösste Hürde**, K3 ebenso
  (in starken Aktienjahren wie 2013, 2019, 2021 verliert D gegen SPY).
- **Sicht 2:** am ehesten hier ein Vorteil, wenn die Sharpe höher ist als die von SPY. Der Hebel läge dann bei etwa
  1.3–2.
- D2/D3 reagieren schneller als D1: kleinere Rückgänge in Crashs (2020), aber mehr Fehlsignale und Kosten in Seitwärtsphasen.
- **Ehrliche Erwartung: D fällt nach Sicht 1 eher durch.** Ein Bestehen wäre eine Überraschung und müsste in B genauso gelten.

## Was ausdrücklich nicht passiert

Kein Hebel. Keine Anpassung von Parametern nach dem Lauf, auch nicht bei knappem Scheitern. Keine weitere Variante
ohne neue Anmeldung. Keine Änderung an C oder an dessen Vorwärtstest.

## Bestätigung

Tim bestätigt, indem er oben die Zeile auf `**Status:** BESTÄTIGT von Tim am <Datum>` ändert und die Zählweise
einträgt (`**Zählweise N:** effektiv` oder `streng`). Alternativ sagt er es Claude im Chat, dann trägt Claude es so ein.
