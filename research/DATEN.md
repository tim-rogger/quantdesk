# Datenbasis Forschungsphase 2 – ETF-Universum

Datenstand **`snapshot-2026-10-09`** (abgerufen 09.10.2026). Prüfbar mit `python research_etf.py daten`.
Code: `quantdesk/etf_data.py` (Universum), `quantdesk/snapshot.py` (Datenstände).

## Warum ETFs

Die Aktien-Studie aus Phase 1 (`data/universe.csv`, 49 Firmen) enthält nur Firmen, die es heute noch gibt.
Jede Kaufstrategie darauf ist nach oben verzerrt. Sie bleibt erhalten, ist aber in allen Berichten als
**„Aktien-Universum MIT Survivorship-Bias“** gekennzeichnet. ETFs bilden ganze Märkte ab und verschwinden
kaum. Damit entfällt der Bias weitgehend, aber nicht ganz (siehe „Ehrliche Grenzen“).

## Datenstände (Reproduzierbarkeit)

Yahoo rechnet `adjclose` rückwirkend neu, z.B. wenn Dividenden nachgetragen werden, und FRED revidiert Reihen.
Ohne eingefrorene Daten liefert derselbe Lauf eine Woche später andere Zahlen. Deshalb gilt:

- Jeder Forschungslauf arbeitet mit einem **benannten Datenstand** `research/data/snapshot-JJJJ-MM-TT/`. Dieser enthält
  die **Rohantworten** der Quellen, unverändert und nur komprimiert, und eine `MANIFEST.json` mit Quelle, URL,
  Abrufzeit, Zeilenzahl, erstem/letztem Tag und SHA-256 je Reihe.
- Jeder Bericht nennt den Datenstand in der Kopfzeile. Beim Lesen wird jede Prüfsumme kontrolliert. Eine veränderte
  Datei bricht den Lauf ab.
- `python research_etf.py snapshot --neu` legt einen neuen Datenstand an. Bestehende werden **nie** überschrieben:
  Am selben Tag entsteht `snapshot-…-2`. Scheitert eine Reihe, entsteht gar nichts.
- `python research_etf.py snapshot --vergleiche ALT NEU` zeigt je Reihe, ob sie sich **rückwirkend** geändert hat:
  Anzahl Tage, grösste Abweichung und Δ Rendite p.a. Bei Yahoo werden Tagesrenditen verglichen. Eine reine
  Neuskalierung von `adjclose` nach einer Dividende ist keine Änderung, neu angehängte Tage auch nicht.
- Die Rohdateien (`*.raw.gz`, ≈ 4 MB) kommen **nicht** ins Git, weil Yahoo die Weitergabe der Daten nicht erlaubt und
  das Repo öffentlich ist. `MANIFEST.json` steht im Git. Damit lässt sich überall prüfen, ob ein lokaler Datenstand
  derselbe ist. Fehlt die Rohdatei, meldet `snapshot --pruefe` das.

## Quellen

| Was | Quelle | Hinweis |
|---|---|---|
| Tageskurse ETFs, Fonds, Indizes | Yahoo Finance (Chart-API) | `adjclose`, also inkl. Dividenden/Zinsen, split-bereinigt. Ausnahmen sind mit „nur Kurs“ markiert. |
| Cash-Zins USD | Yahoo `^IRX` (13-Wochen-T-Bill), ab 1966 | wie in Phase 1 |
| USD/CHF | FRED `DEXSZUS` (Fed, Mittagskurs New York), täglich ab 1971 | **nicht** Yahoo `CHF=X`: Die Reihe beginnt erst 2003 und weicht an einzelnen Tagen bis 12 % ab (Ø 0.29 %). |
| Zins CHF | FRED `IRSTCI01CHM156N` (Tagesgeld) bis 06/1999, ab 07/1999 `IR3TIB01CHM156N` (3-Monats-Interbank), monatlich | für die Sharpe in CHF. In Phase 1 war der CHF-Zins vereinfacht 0 (2019–2026 vertretbar, ab 1995 nicht mehr). |

## Anlageklassen und Verkettung

Verkettung: Bis zum Tag vor dem ersten ETF-Kurs zählen die **Renditen** der Ersatzreihe, ab dann der ETF.
Die Ersatzreihe wird so skaliert, dass sie am ersten gemeinsamen Tag ohne Sprung in den ETF übergeht.
Jede Reihe kennt ihre Segmente (`ChainedSeries.segments`). Für jeden Tag ist abfragbar, woher der Kurs stammt.
Die Überlappungs-Kennzahlen rechnet der Code auf dem jeweiligen Datenstand (`overlap_stats`) aus. Die Werte
unten gelten für `snapshot-2026-10-09` und sind aus Monatsrenditen im gemeinsamen Zeitraum berechnet.

| ETF | Anlageklasse | Daten ab | ETF ab | Ersatzreihe davor | Korr. | Abw. p.a. | Tracking Error |
|---|---|---|---|---|---|---|---|
| SPY | US-Aktien | 1980-01 | 1993-01 | VFINX (Vanguard 500 Index) | 0.998 | +0.00 % | 0.8 % |
| EFA | Aktien Industrieländer ex US | 1990-06 | 2001-08 | 60 % VEURX + 40 % VPACX (Vanguard European/Pacific Index), täglich rebalanciert ¹ | 0.994 | +0.52 % | 1.9 % |
| EEM | Aktien Schwellenländer | 1994-05 | 2003-04 | VEIEX (Vanguard Emerging Markets Index) | 0.978 | −0.16 % | 4.3 % |
| IEF | US-Staatsanleihen 7–10 J. | 1991-10 | 2002-07 | VFITX (Vanguard Intermediate-Term Treasury) | 0.982 | −0.29 % | 2.2 % |
| TLT | US-Staatsanleihen lang | 1986-05 | 2002-07 | VUSTX (Vanguard Long-Term Treasury) | 0.992 | +0.06 % | 2.2 % |
| LQD | US-Unternehmensanleihen | 1993-10 | 2002-07 | VFICX (Vanguard Interm.-Term Investment-Grade) ² | 0.917 | −0.19 % | 3.6 % |
| GLD | Gold | 2000-08 | 2004-11 | GC=F (Gold-Futures, vorderster Kontrakt, nur Kurs) ³ | 0.992 | +0.47 % | 2.2 % |
| DBC | Rohstoffe | 2002-07 | 2006-02 | PCRIX (PIMCO CommodityRealReturn) ⁴ | 0.906 | −0.52 % | 8.2 % |
| VNQ | US-Immobilien (REITs) | 1996-05 | 2004-09 | VGSIX (Vanguard REIT Index) | 0.999 | −0.10 % | 0.8 % |
| Cash | T-Bill | 1966 | – | `^IRX` | | | |

Nur für die Kandidaten E und F, nicht Teil des Trend-Universums:

| ETF | Daten ab | ETF ab | Ersatzreihe |
|---|---|---|---|
| QQQ | 1985-10 | 1999-03 | ^NDX (Index, nur Kurs, ohne Dividenden): Korr. 0.999, −0.60 % p.a. |
| USMV / SPLV / QUAL | 2011-10 / 2011-05 / 2013-07 | gleich | keine – kurze Historie (deshalb ist F nachrangig) |

¹ Die Gewichte entsprechen ungefähr den EAFE-Regionen in den 1990ern (Europa 55–65 %, Pazifik 35–45 %). Anfang der
1990er war Japan noch schwerer gewichtet. In der Japan-Baisse ist der Ersatz deshalb eher etwas zu gut.
² besser passend als VWESX (lange Laufzeit): Korr. 0.916, +0.33 % p.a.
³ Vor 08/2000 gibt es keine freie Tagesreihe für Gold. Minenfonds (VGPMX, ^XAU) sind kein Gold und werden nicht verwendet.
⁴ Der S&P-GSCI-Index `^SPGSCI` (ab 1984) wird **nicht** verwendet. Er ist ein Spot-Index ohne Rollkosten und
lag ab 2006 3.6 % p.a. über dem investierbaren GSG. Vor 07/2002 gibt es deshalb keine Rohstoffe.

## Drei Fassungen je Kandidat (Empfindlichkeit gegenüber den Ersatzreihen)

Die Ersatzreihen weichen in der Überlappung um bis zu 0.6 % p.a. ab. Über viele verkettete Jahre summiert sich das.
Darum wird **jeder** Kandidat in drei Fassungen ausgewertet, nebeneinander in derselben Tabelle
(`python research_etf.py daten --fassung A|B|C`):

| Fassung | Daten | Alle 9 Klassen ab | mit 1 Jahr Vorlauf ab | Zweck |
|---|---|---|---|---|
| **A** | volle verkettete Historie | 2002-07 | 2003-07 | möglichst viele Testjahre |
| **B** | **nur echte ETF-Daten**, keine Ersatzreihe | **2006-02** (DBC) | **2007-02** | hängt das Ergebnis an der Verkettung? |
| **C** | wie A, Ersatzreihen um ihre gemessene Abweichung korrigiert | 2002-07 | 2003-07 | Richtung und Grösse des Fehlers |

**Korrektur gegenüber dem Auftrag:** Fassung B beginnt nicht 2004. Mit echten ETFs sind alle neun Klassen erst ab
Februar 2006 vorhanden, weil DBC dann startet (GLD: 11/2004, VNQ: 09/2004). Mit einem Jahr Vorlauf wird ab Februar 2007
getestet. Das ergibt rund 19.5 Testjahre statt 21. 2008, 2020 und 2022 sind dabei, 2000–2002 nicht.

Fassung C verschiebt jede Ersatzreihe um ihre Abweichung p.a. in der Überlappung, z.B. VEURX/VPACX um −0.49 % p.a.,
PCRIX um +0.51 % p.a. Das korrigiert nur den **durchschnittlichen** Fehler, nicht Abweichungen in einzelnen Phasen
(z.B. das zu tiefe Japan-Gewicht Anfang der 1990er).

**Regel:** Weichen A und B im Urteil voneinander ab, gilt **B**, und der Unterschied wird im Fazit benannt.

## Verfügbarkeit in Fassung A (≥ 1 Jahr Historie als Vorlauf, jeweils Anfang Jahr)

| Ab | Anlageklassen | Neu |
|---|---|---|
| 1996 | 6/9 | SPY, EFA, EEM, IEF, TLT, LQD |
| 1998 | 7/9 | + VNQ |
| 2002 | 8/9 | + GLD |
| 2004 | 9/9 | + DBC |

## Datenqualität

Gemeldet wird jede Tagesbewegung über 25 % und jede Lücke über 10 Tage. Die Daten werden dabei nicht verändert.
Auf `snapshot-2026-10-09`: **keine Warnungen**.

## Ehrliche Grenzen

- **Die Auswahl der Anlageklassen ist Rückschau.** Dass heute genau diese neun gewählt werden, ist selbst eine
  Entscheidung mit Wissen von heute. Ein Anleger von 1996 hätte Japan deutlich schwerer gewichtet und Rohstoffe oder
  Schwellenländer vielleicht gar nicht im Blick gehabt. Diese Verzerrung ist kleiner als bei Einzelaktien, weil
  ganze Märkte statt überlebender Firmen gewählt werden, aber sie ist **nicht null**.
- **Das Universum wächst** (in Fassung A von 6 auf 9 Klassen). Wird ab 1996 getestet, ändert sich die Strategie mitten
  im Test, und am Ende ist unklar, was gemessen wurde. Vorschlag zur Anmeldung (Teil 2): **Hauptauswertung mit allen
  neun Klassen** (Fassung A ab 2004, Fassung B ab 2007). Dazu eine **Nebenauswertung ab 1996 mit wachsendem
  Universum**, die nur zeigt, wie es in 2000–2002 gelaufen wäre. Das Urteil hängt nie an der Nebenauswertung.
- Die Ersatzreihen sind Fonds mit eigenen Kosten (0.1–0.9 % p.a.) und leicht anderer Zusammensetzung. Ihre Abweichung
  ist oben gemessen und wird mit Fassung C geprüft.
- Die Fonds-Rücknahmepreise (NAV) werden zum Schlusskurs gebildet. Eröffnungskurse gibt es dort nicht
  (Open = Schluss). Für Kandidat E (Ausführung am Folgetag zur Eröffnung) zählt deshalb nur die echte ETF-Historie
  (SPY ab 1993, QQQ ab 1999).
