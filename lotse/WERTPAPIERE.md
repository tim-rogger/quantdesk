# Lotse – Wertpapiere zur Auswahl

Stand 10.10.2026. **Nichts davon steht in `config.toml`**, Tim wählt selbst. Alle Vorschläge handeln an der SIX
(bei IBKR `EBS`) in CHF.

## Vorher geklärt: Bruchstücke an der SIX

**Ja.** IBKR erlaubt Bruchstücke für SIX-Papiere. In der offiziellen Liste der in Bruchstücken handelbaren Papiere
([`fracshare_stk.csv`](https://www.ibkr.com/download/fracshare_stk.csv), heruntergeladen am 10.10.2026) stehen
**675 Einträge mit Börse `EBS`**, darunter alle sechs Vorschläge unten. Bei keinem ist ein Enddatum
(`SCHEDULED_INELIGIBILTY_DATE`) eingetragen.

Bedingungen und Vorbehalte:
- Im Konto müssen die Handelserlaubnis für europäische Aktien und „Trade in Fractions“ eingeschaltet sein. Im
  Paper-Konto ebenfalls prüfen.
- IBKR lässt nur liquide und grosse Papiere in Bruchstücken handeln. Bei der Einführung 2022 hiess das: Ø
  Tagesvolumen über 5 Mio. USD und Börsenwert über 5 Mrd. USD. Die Liste kann sich ohne Vorankündigung ändern.
- **Nicht geprüft:** ob die TWS-API (IB Gateway) Bruchstück-Orders an `EBS` annimmt. Das zeigt erst eine erste
  Order im PAPER-Modus. Geht es nicht, setzt `bruchstueck_stellen = 0` auf ganze Stück zurück. Dann reichen
  100 CHF bei einem Kurs von etwa 155 CHF (VWRL) für gar nichts, und Mindestbetrag und Regel 9 müssen angepasst werden.

## Vorschläge (je ein Aktien- und ein Anleihen-Papier)

| | Aktien (Wertpapier 1) | Anleihen (Wertpapier 2) | Eigenschaft |
|---|---|---|---|
| **A – einfach, ausschüttend** | VWRL | CHCORP | Weltaktien in USD, Anleihen in CHF |
| **B – thesaurierend** | SSAC | AGGS | keine Ausschüttungen, also kaum unerwartetes Cash |
| **C – ohne Währungsrisiko** | ACWIS | CSBGC7 | alles in CHF abgesichert bzw. Schweizer Anleihen |

| Kürzel | Name | Domizil | Ertrag | TER | Fondsgrösse | Bruchstücke |
|---|---|---|---|---|---|---|
| VWRL | Vanguard FTSE All-World UCITS ETF | Irland | ausschüttend (quartalsweise, in **USD**) | 0.14 % ¹ | 87.3 Mrd. USD (Klasse 27.1 Mrd.) ¹ | ✓ |
| SSAC | iShares MSCI ACWI UCITS ETF | Irland | thesaurierend | 0.20 % ² | ≈ 18 Mrd. USD ² | ✓ |
| ACWIS | UBS MSCI ACWI SF UCITS ETF, CHF hedged | Irland | thesaurierend | 0.21 % ³ | ≈ 1.5 Mrd. CHF ³ | ✓ |
| CHCORP | iShares Core CHF Corporate Bond ETF (CH) | Schweiz | ausschüttend (CHF) | 0.15 % ⁴ | ≈ 2.2 Mrd. EUR ⁴ | ✓ |
| AGGS | iShares Core Global Aggregate Bond UCITS ETF, CHF hedged | Irland | thesaurierend | 0.10 % ⁵ | Klasse 0.97 Mrd. CHF, Fonds 14.1 Mrd. USD ⁵ | ✓ |
| CSBGC7 | iShares Swiss Domestic Government Bond 3-7 (CH) | Schweiz | ausschüttend (CHF) | 0.15 % ⁴ | ≈ 0.43 Mrd. CHF ⁴ | ✓ |

**Spread (Unterschied Kauf-/Verkaufskurs):** Selbst gemessen habe ich ihn nicht. Ein Ratgeber nennt für grosse
Welt-ETFs an der SIX typischerweise etwa 0.05 %, die Quelle ist aber rund zwei Jahre alt. Die SIX veröffentlicht für
jedes ETF den durchschnittlichen Spread pro Tag (Market Quality Metrics,
[six-group.com/…/statistics](https://www.six-group.com/en/market-data/statistics.html)). **Bitte vor der Wahl für
die engere Auswahl dort nachsehen.** Bei Monatskäufen von ein paar hundert Franken ist der Spread neben der Gebühr
der grösste Kostenposten.

¹ Vanguard Schweiz, Produktseite, abgerufen 10.10.2026. ² iShares-Factsheet, Datum nicht angegeben, eher älter.
³ Zonebourse, 27.02.2026. „SF“ heisst synthetisch: Der Fonds bildet den Index über einen Swap nach, er hält die
Aktien also nicht direkt. ⁴ Trackinsight, Hargreaves Lansdown u.a., 2026. Die Grössenangaben verschiedener Quellen
weichen voneinander ab. ⁵ iShares-Factsheet, Daten per 31.07.2026.

## Was die Wahl beeinflusst (kurz)

1. **Ausschüttungen in USD (VWRL):** Die Dividende kommt als **USD-Cash** aufs Konto. Lotse rechnet nur mit
   CHF-Cash (`TotalCashValue` in CHF), die Dollar blieben also liegen, bis jemand sie wechselt. Mit thesaurierenden
   Fonds (Paar B, C) gibt es dieses Problem nicht, und Punkt 3 aus dem Auftrag (Einzahlung oder Dividende) wird
   fast gegenstandslos.
2. **Währungsabsicherung (Paar C, AGGS):** Die Absicherung in CHF kostet ungefähr die Zinsdifferenz zwischen USD und
   CHF, derzeit grob 3 % pro Jahr auf den abgesicherten Teil. Bei Anleihen ist das üblich und sinnvoll. Bei Aktien
   ist es eine bewusste Entscheidung gegen Rendite und für weniger Schwankung in CHF.
3. **Quellensteuer:** Irische Fonds verlieren auf US-Dividenden 15 % im Fonds, das lässt sich nicht zurückholen.
   Schweizer Fonds (CHCORP, CSBGC7) haben 35 % Verrechnungssteuer, die man mit der Steuererklärung zurückbekommt.
4. **Stempelsteuer:** Nach meinem Kenntnisstand fällt bei IBKR keine Schweizer Umsatzabgabe an, weil IBKR kein
   Schweizer Effektenhändler ist. Bitte selbst bestätigen.

## Zur Annahme im Auftrag: „US-ETFs gehen nicht“

Diese Annahme ist **für die Schweiz wohl nicht richtig.** PRIIPs ist eine EU-Regel. Nach mehreren Quellen von 2026
können Personen mit Wohnsitz in der Schweiz bei IBKR weiterhin US-ETFs wie VT kaufen. Es gibt aber einzelne Berichte
über „Trading Restricted“, und eine Garantie für die Zukunft gibt es nicht. Bei einem US-ETF wäre die US-Quellensteuer
über das Formular DA-1 teilweise zurückzuholen, das ist ein Vorteil gegenüber irischen Fonds. Dafür fällt bei jeder
Einzahlung ein Währungswechsel CHF → USD an (bei IBKR mindestens etwa 2 USD pro Wechsel), und es gibt das Thema
US-Nachlasssteuer. **Für Stufe 1 schlage ich deshalb nur SIX-Papiere in CHF vor**, wie im Auftrag gewünscht.

Quellen:
- [IBKR fracshare_stk.csv](https://www.ibkr.com/download/fracshare_stk.csv)
- [IBKR Pressemitteilung 2022, Bruchstücke für europäische Aktien/ETFs](https://www.businesswire.com/news/home/20220531005157/en/Interactive-Brokers-Introduces-Fractional-Shares-Trading-in-European-Stocks-and-ETFs)
- [Investment Moats zu den Bedingungen für Bruchstücke](https://investmentmoats.com/money/interactive-brokers-trade-fractional-european-shares-and-etfs/)
- [IBKR FAQ Fractional Shares](https://www.interactivebrokers.com/lib/cstools/faq/#/content/1163260722)
- [Vanguard Schweiz, VWRL](https://www.ch.vanguard/en/private-investor/product/etf/equity/9505/ftse-all-world-ucits-etf-usd-distributing)
- [iShares Factsheet SSAC](https://blackrock.com/ch/individual/en/literature/fact-sheet/ssac-ishares-msci-acwi-ucits-etf-fund-fact-sheet-de-ch.pdf)
- [iShares Factsheet AGGS](https://www.ishares.com/ch/professionelle-anleger/de/literature/fact-sheet/aggs-ishares-core-global-aggregate-bond-ucits-etf-fund-fact-sheet-de-ch.pdf)
- [Zonebourse ACWIS](https://ch.zonebourse.com/cours/etf/UBS-MSCI-ACWI-SF-UCITS-ET-25699577/cotations/)
- [Trackinsight CHCORP](https://www.trackinsight.com/en/fund/CHCORP)
- [Trackinsight CSBGC7](https://www.trackinsight.com/en/fund/CSBGC7)
- [SIX Statistiken / Market Quality Metrics](https://www.six-group.com/en/market-data/statistics.html)
- [The Poor Swiss: US-ETFs für Schweizer Anleger](https://thepoorswiss.com/swiss-investors-lose-access-us-domiciled-etfs/)
- [schweizerfinanzblog: IBKR Schweiz](https://schweizerfinanzblog.ch/en/interactive-brokers-switzerland-experience/)
