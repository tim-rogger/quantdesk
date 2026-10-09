# Datenbasis Forschungsphase 2 – ETF-Universum

Stand 09.10.2026. Erzeugt und jederzeit prüfbar mit `python research_etf.py daten`. Code: `quantdesk/etf_data.py`.

## Warum ETFs

Die Aktien-Studie aus Phase 1 (`data/universe.csv`, 49 Firmen) enthält nur Firmen, die es heute noch gibt.
Jede Kaufstrategie darauf ist nach oben verzerrt. Sie bleibt erhalten, ist aber in allen Berichten als
**„Aktien-Universum MIT Survivorship-Bias“** gekennzeichnet. ETFs bilden ganze Märkte ab und verschwinden
kaum. Damit entfällt der Bias weitgehend.

## Quellen

| Was | Quelle | Hinweis |
|---|---|---|
| Tageskurse ETFs, Fonds, Indizes | Yahoo Finance (Chart-API) | `adjclose`, also inkl. Dividenden/Zinsen (Total Return), split-bereinigt. Ausnahmen sind mit „nur Kurs“ markiert. |
| Cash-Zins USD | Yahoo `^IRX` (13-Wochen-T-Bill), ab 1966 | wie in Phase 1 |
| USD/CHF | FRED `DEXSZUS` (Fed, Mittagskurs New York), täglich ab 1971 | **nicht** Yahoo `CHF=X`: Die Reihe beginnt erst 2003 und weicht an einzelnen Tagen bis 12 % ab (Ø 0.29 %). |
| Zins CHF | FRED `IRSTCI01CHM156N` (Tagesgeld) bis 06/1999, ab 07/1999 `IR3TIB01CHM156N` (3-Monats-Interbank), monatlich | für die Sharpe in CHF. In Phase 1 war der CHF-Zins vereinfacht 0 (2019–2026 vertretbar, ab 1995 nicht mehr). |

## Anlageklassen und Verkettung

Verkettung: Bis zum Tag vor dem ersten ETF-Kurs zählen die **Renditen** der Ersatzreihe, ab dann der ETF.
Die Ersatzreihe wird so skaliert, dass sie am ersten gemeinsamen Tag ohne Sprung in den ETF übergeht.
Jede Reihe kennt ihre Segmente (`ChainedSeries.segments`). Für jeden Tag ist abfragbar, woher der Kurs stammt.

| ETF | Anlageklasse | Daten ab | ETF ab | Ersatzreihe davor | Überlappung mit dem ETF (Monatsrenditen) |
|---|---|---|---|---|---|
| SPY | US-Aktien | 1980-01 | 1993-01 | VFINX (Vanguard 500 Index) | Korr. 0.998, +0.01 % p.a. |
| EFA | Aktien Industrieländer ex US | 1990-06 | 2001-08 | 60 % VEURX + 40 % VPACX (Vanguard European/Pacific Index), täglich rebalanciert | Korr. 0.994, +0.54 % p.a. ¹ |
| EEM | Aktien Schwellenländer | 1994-05 | 2003-04 | VEIEX (Vanguard Emerging Markets Index) | Korr. 0.978, −0.18 % p.a. |
| IEF | US-Staatsanleihen 7–10 J. | 1991-10 | 2002-07 | VFITX (Vanguard Intermediate-Term Treasury) | Korr. 0.982, −0.28 % p.a. |
| TLT | US-Staatsanleihen lang | 1986-05 | 2002-07 | VUSTX (Vanguard Long-Term Treasury) | Korr. 0.992, +0.07 % p.a. |
| LQD | US-Unternehmensanleihen | 1993-10 | 2002-07 | VFICX (Vanguard Interm.-Term Investment-Grade) | Korr. 0.917, −0.19 % p.a. ² |
| GLD | Gold | 2000-08 | 2004-11 | GC=F (Gold-Futures, vorderster Kontrakt, nur Kurs) | Korr. 0.992, +0.49 % p.a. ³ |
| DBC | Rohstoffe | 2002-07 | 2006-02 | PCRIX (PIMCO CommodityRealReturn) | Korr. 0.906, −0.50 % p.a. ⁴ |
| VNQ | US-Immobilien (REITs) | 1996-05 | 2004-09 | VGSIX (Vanguard REIT Index) | Korr. 0.999, −0.10 % p.a. |
| Cash | T-Bill | 1966 | – | `^IRX` | – |

Nur für die Kandidaten E und F, nicht Teil des Trend-Universums:

| ETF | Daten ab | ETF ab | Ersatzreihe |
|---|---|---|---|
| QQQ | 1985-10 | 1999-03 | ^NDX (Index, nur Kurs, ohne Dividenden): Korr. 0.999, −0.59 % p.a. |
| USMV / SPLV / QUAL | 2011-10 / 2011-05 / 2013-07 | gleich | keine – kurze Historie (deshalb ist F nachrangig) |

¹ Die Gewichte entsprechen ungefähr den EAFE-Regionen in den 1990ern (Europa 55–65 %, Pazifik 35–45 %). Anfang der
1990er war Japan noch schwerer gewichtet. In der Japan-Baisse ist der Ersatz deshalb eher etwas zu gut.
² besser passend als VWESX (lange Laufzeit): Korr. 0.916, +0.33 % p.a.
³ Vor 08/2000 gibt es keine freie Tagesreihe für Gold. Minenfonds (VGPMX, ^XAU) sind kein Gold und werden nicht verwendet.
⁴ Der S&P-GSCI-Index `^SPGSCI` (ab 1984) wird **nicht** verwendet. Er ist ein Spot-Index ohne Rollkosten und
lag ab 2006 3.6 % p.a. über dem investierbaren GSG. Vor 07/2002 gibt es deshalb keine Rohstoffe.

## Verfügbarkeit (≥ 1 Jahr Historie als Vorlauf, jeweils Anfang Jahr)

| Ab | Anlageklassen | Neu |
|---|---|---|
| 1996 | 6/9 | SPY, EFA, EEM, IEF, TLT, LQD |
| 1998 | 7/9 | + VNQ |
| 2002 | 8/9 | + GLD |
| 2004 | 9/9 | + DBC |

Zum Vergleich: In Phase 1 waren es 7 Testjahre (Okt 2019 – Sep 2026). Ab 1996 sind rund **30 Testjahre**
möglich, darunter 2000–2002, 2008, 2020 und 2022.

## Datenqualität

Gemeldet wird jede Tagesbewegung über 25 % und jede Lücke über 10 Tage. Die Daten werden dabei nicht verändert.
Stand 09.10.2026: **keine Warnungen**.

## Ehrliche Grenzen

- Die Ersatzreihen sind Fonds mit eigenen Kosten (0.1–0.9 % p.a.) und leicht anderer Zusammensetzung. Gezeigt
  wird jeweils die Abweichung in der Überlappung. Sie liegt bei allen Reihen unter 0.6 % p.a.
- Die Fonds-Rücknahmepreise (NAV) werden zum Schlusskurs gebildet. Eröffnungskurse gibt es dort nicht
  (Open = Schluss). Für Kandidat E (Ausführung am Folgetag zur Eröffnung) zählt deshalb nur die echte ETF-Historie
  (SPY ab 1993, QQQ ab 1999).
- Das Universum wächst mit der Zeit (6 → 9 Anlageklassen). Wie das im Test behandelt wird, wird bei der Anmeldung
  der Kandidaten festgelegt (Teil 2).
