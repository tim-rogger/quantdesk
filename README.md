# QuantDesk AI Bot

[![tests](https://github.com/tim-rogger/quantdesk/actions/workflows/ci.yml/badge.svg)](https://github.com/tim-rogger/quantdesk/actions/workflows/ci.yml)

Ein AI Trading Bot mit Tkinter-Oberfläche: Er fährt eine **Martingale/DCA-Grid-Strategie** auf deinem
**IBKR-Paper-Konto** und hat einen eingebauten **AI Portfolio Manager (Claude)**, dem du Fragen zu deinem
Portfolio stellen kannst.

Basiert auf [AI_Trading_Bot von Roman Paolucci](https://github.com/romanmichaelpaolucci/AI_Trading_Bot)
aus dem Video [„How to Build an AI Trading Bot“](https://www.youtube.com/watch?v=_87QHZXOOKA) –
umgebaut von Alpaca auf **Interactive Brokers (Client Portal Gateway)** und von OpenAI auf **Claude**,
mit vielen Bugfixes (siehe unten).

> Dieses Repo war früher ein Java/Spring-Boot-Projekt (Backtester, Momentum-Scanner). Der alte Stand liegt in der Git-History.

## Sicherheit – zuerst lesen

- **Nur Paper Trading.** Es gibt keinen Echtgeld-Modus. Der Bot startet nur mit IBKR-Konten, die mit **`DU`** beginnen
  (Paper-Konten) und prüft zusätzlich, dass genau dieses Konto im Gateway-Login vorhanden ist.
- **Zwei Modi:** `DRY_RUN` (Standard – alles wird berechnet und geloggt, **keine** Order geht an IBKR)
  und `PAPER` (Orders gehen an dein Paper-Konto). Der Modus steht gross im Fenstertitel und farbig oben im Fenster.
- **STOP ALL** (roter Button): schaltet sofort alle Systeme aus und pausiert die Engine. Bereits bei IBKR liegende
  Orders bleiben offen – die kannst du beim Entfernen einer Equity stornieren lassen oder im IBKR-Portal löschen.
- Maximal **20 Levels** pro Symbol, Levels × Drawdown muss unter 100 % bleiben. Stückzahl pro Order: `QUANTDESK_ORDER_QTY` (Standard 1).
- **Claude platziert niemals Orders.** Der AI Portfolio Manager liest nur und analysiert.
- **Risiko der Strategie:** Martingale/DCA kauft in fallende Kurse nach. Es gibt **keinen Stop-Loss** – fällt eine Aktie
  weiter, wächst die Position (und das Risiko) mit jedem Level. Das ist ein Lern-/Experimentierprojekt,
  **keine Anlageberatung**.

## Was der Bot macht

**Die Oberfläche** (von oben nach unten):

1. **Modus-Banner** (gelb = `DRY_RUN`, orange = `PAPER`), rechts daneben der IBKR-Verbindungsstatus und dein Cash.
2. **Formular**: `Symbol`, `Levels`, `Drawdown%` und der Button **Add Equity**.
3. **Tabelle**: `Symbol`, `Position`, `Entry Price`, `Last` (letzter Preis + Quelle: IBKR, Stooq oder Yahoo),
   `Levels` (Preise mit Status: ○ ausstehend, ● platziert, ✓ gefüllt, ✗ storniert) und `Status` (On/Off).
4. Buttons **Toggle Selected System**, **Remove Selected Equity**, **STOP ALL** und **Resume Engine**.
5. **Bot-Log**: alles, was der Bot tut (Orders grün, Warnungen orange, Fehler rot).
6. **Chat mit Claude**: Frage eingeben, *Send* (oder Enter) – die Antwort erscheint darunter.

**Die Strategie (Martingale/DCA-Grid wie im Video):**

1. Du schaltest ein System auf **On**. Hat das Konto noch keine Position im Symbol, kauft der Bot **1 Aktie per Market-Order**.
   Hältst du die Aktie schon, wird dein bestehender Durchschnittspreis als Einstiegspreis übernommen.
2. Sobald der Einstieg gefüllt ist, wird der **Einstiegspreis einmal** gespeichert: der Ausführungspreis der Market-Order
   (ohne Kommission). Bei übernommenen Positionen ist es der IBKR-Durchschnittspreis, der die Kommission enthält.
3. Darunter legt der Bot **Limit-Buy-Orders (GTC)** – je eine pro Level:
   `Preis Level i = Einstiegspreis × (1 − Drawdown × i)`.
   Beispiel AAPL, Einstieg 200 $, 3 Levels, 2 %: 196.00, 192.00, 188.00.
4. Jedes Level wird **genau einmal** platziert. Gefüllte oder stornierte Orders werden erkannt und angezeigt.
5. Der Bot prüft das alle `QUANTDESK_INTERVAL_SECONDS` Sekunden im Hintergrund (Video: 5 s, Standard hier: 15 s).

**AI Portfolio Manager:** Bei jeder Frage schickt der Bot deine Positionen, offenen Orders, Cash, die Grid-Konfiguration
und (optional) die letzten 5 Yahoo-Schlagzeilen pro gehaltener Aktie an Claude – mit dem Prompt aus dem Video
(Risiko-Exposure, offene Limit-Orders, Portfolio-Gesundheit, Marktausblick, Risikomanagement). Die letzten 6 Chat-Nachrichten bleiben als Verlauf erhalten.

## Was du brauchst (Schritt für Schritt, Windows)

### 1. Programme installieren

- **Python 3.11 oder neuer** von [python.org](https://www.python.org/downloads/). Tkinter ist dabei.
  Im Installer unbedingt **„Add python.exe to PATH“** anhaken.
- **Java** für das IBKR Gateway, z.B. [Temurin 17+](https://adoptium.net/).

### 2. IBKR Client Portal Gateway starten

1. Auf der IBKR-Webseite „Client Portal API“ die Datei **`clientportal.gw.zip`** herunterladen und entpacken (z.B. nach `C:\ibkr\clientportal.gw`).
2. Eine Eingabeaufforderung in diesem Ordner öffnen und starten:
   ```bat
   bin\run.bat root\conf.yaml
   ```
   Das Fenster offen lassen, solange der Bot läuft.
3. Im Browser **https://localhost:5000** öffnen, die Zertifikatswarnung akzeptieren („Erweitert → Weiter zu localhost“)
   und dich mit deinem **Paper-Trading-Login** anmelden. Danach steht dort „Client login succeeds“.
4. Deine **Paper-Konto-ID** (`DU…`) findest du im IBKR-Portal oben rechts bzw. in der Kontoübersicht.

> **Wichtig:** Bist du gleichzeitig mit demselben Benutzer in TWS oder der IBKR-App eingeloggt, kann die Gateway-Session
> abbrechen („competing session“). Die Session läuft ausserdem nach einer Weile ab (spätestens täglich) – dann einfach
> im Browser neu einloggen. Der Bot hält sie mit `/tickle` am Leben und zeigt im Log an, wenn du dich neu anmelden musst.

### 3. Anthropic-API-Key

Auf [console.anthropic.com](https://console.anthropic.com) einen API-Key erstellen (Abrechnung nach Verbrauch).
Ohne Key läuft der Bot trotzdem – nur der Chat zeigt dann eine Fehlermeldung.

### 4. Bot installieren

In einer Eingabeaufforderung im Repo-Ordner:

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Dann `.env` im Editor öffnen und ausfüllen: `IBKR_ACCOUNT_ID=DU…`, `ANTHROPIC_API_KEY=…`.
`.env` steht in `.gitignore` und landet nie auf GitHub.

### 5. Starten

```bat
python bot.py
```

oder einfach **`start.bat`** doppelklicken.

1. **Zuerst mit `QUANTDESK_MODE=DRY_RUN`** (Standard): Symbol hinzufügen (z.B. `AAPL`, 3 Levels, 2 %), auswählen,
   *Toggle Selected System*. Im Log siehst du, welche Orders der Bot senden **würde** („DRY_RUN … NICHT gesendet“).
   Tipp: Mit leerer `IBKR_ACCOUNT_ID` läuft `DRY_RUN` sogar ganz ohne Gateway (Preise dann von Stooq/Yahoo).
2. Wenn alles passt: in `.env` auf `QUANTDESK_MODE=PAPER` stellen und neu starten. Jetzt gehen die Orders an dein Paper-Konto.

> Beim Neustart (z.B. Wechsel von `DRY_RUN` zu `PAPER`) wird der simulierte Zustand (Fake-Einstieg, Fake-Orders)
> automatisch zurückgesetzt – die Konfiguration (Symbol, Levels, Drawdown) bleibt.

### Desktop-Verknüpfung („Deploy!“)

Das Gateway braucht deinen lokalen Browser-Login, deshalb läuft der Bot auf deinem Laptop – kein Cloud-Deployment.
Damit der Start ein Klick ist: Rechtsklick auf `start.bat` → **Weitere Optionen anzeigen → Verknüpfung erstellen**,
die Verknüpfung auf den Desktop ziehen und z.B. in „QuantDesk AI Bot“ umbenennen.
Ablauf danach: Gateway starten → im Browser einloggen → Desktop-Verknüpfung doppelklicken.

## Bedienung

| Aktion | So geht's |
|---|---|
| Equity hinzufügen | Symbol, Levels (1–20), Drawdown in % eingeben → **Add Equity**. Status ist zuerst *Off*. |
| System ein/aus | Zeile(n) auswählen → **Toggle Selected System**. |
| Entfernen | Zeile auswählen → **Remove Selected Equity**. Bei offenen Orders fragt der Bot, ob er sie stornieren soll. |
| Notbremse | **STOP ALL** – alle Systeme *Off*, Engine pausiert. Mit **Resume Engine** weiter. |
| Claude fragen | Frage ins Chatfeld, **Send**. Der Button ist gesperrt, bis die Antwort da ist. |

Das Log landet zusätzlich in `quantdesk.log`.

### Aufbau von `equities.json`

```json
{
  "version": 1,
  "systems": [
    {
      "symbol": "AAPL",
      "num_levels": 3,
      "drawdown": 0.02,
      "status": "On",
      "position": 1.0,
      "entry_price": 200.0,
      "entry_order_id": null,
      "entry_order_time": null,
      "levels": [
        {"level": 1, "price": 196.0, "status": "placed", "order_id": "1234567"},
        {"level": 2, "price": 192.0, "status": "pending", "order_id": null},
        {"level": 3, "price": 188.0, "status": "pending", "order_id": null}
      ],
      "simulated": false
    }
  ]
}
```

Level-Status: `pending` (noch nicht platziert) → `placed` → `filled` oder `cancelled`. Die Datei wird atomar
geschrieben; ist sie kaputt, wird sie als `*.corrupt.json` gesichert und der Bot startet mit leerer Liste.

## Aufbau des Codes

```
bot.py                      Tkinter-GUI (Einstiegspunkt)
start.bat                   Doppelklick-Start
quantdesk/
  config.py                 Einstellungen aus .env, DU-Konto-Pflicht
  app.py                    setzt Broker, Marktdaten, Engine und AI zusammen
  broker/base.py            Broker-Interface + Datentypen (Position, Order)
  broker/ibkr.py            IBKR Client Portal REST-Client
  broker/dry_run.py         DRY_RUN-Wrapper (liest echt, sendet nichts) + Offline-Broker
  marketdata.py             Preis: IBKR-Snapshot → Stooq → Yahoo
  news.py                   Yahoo-RSS-Schlagzeilen
  strategy.py               reine Grid/DCA-Logik (keine I/O)
  storage.py                equities.json atomar laden/speichern
  engine.py                 Hintergrund-Loop, thread-sicher, STOP ALL
  ai.py                     Claude Portfolio Manager
tests/                      pytest (ohne Netzwerk, ohne tkinter)
```

**Marktdaten:** Paper-Konten haben oft keine Marktdaten-Abos, dann liefert der IBKR-Snapshot nichts und der Bot nimmt
den letzten Schlusskurs von Stooq. Stooq blockiert automatisierte Abrufe inzwischen allerdings häufig mit einer
Browser-Prüfung – deshalb gibt es als zweiten Fallback die Yahoo-Chart-API. Welche Quelle genutzt wurde, steht in der Spalte `Last`.

## Bugfixes gegenüber dem Original aus dem Video

1. Levels wuchsen bei jedem Durchlauf endlos (bis > 50 Levels) → Anzahl wird separat als `num_levels` gespeichert.
2. Negative Preise (bis −362) → Eingaben werden validiert (Levels 1–20, Levels × Drawdown < 100 %), Preise auf 2 Nachkommastellen.
3. JSON-Keys wurden nach dem Laden zu Strings → doppelte Orders. Jetzt strukturierte Level-Einträge mit Status und Order-ID.
4. `fetch_open_orders()` gab `None` zurück → Claude bekommt die echten offenen Orders.
5. `side` war hart auf `buy` codiert → echte Side vom Broker.
6. `messagebox` aus dem Hintergrund-Thread (Tkinter ist nicht thread-safe) → Events über `queue.Queue`, GUI pollt per `after()`.
7. Der Chat blockierte die GUI → Claude läuft im Thread, *Send* ist währenddessen gesperrt.
8. Bei **jedem** Fehler (auch Netzwerk) wurde ein neuer Market-Buy platziert → gekauft wird nur bei sicher „keine Position“,
   ohne gespeicherten Einstieg und ohne offene Einstiegs-Order. Kann das Konto nicht gelesen werden, wird die Runde übersprungen.
9. Einstiegspreis = Maximum der letzten 50 Orders → Einstiegspreis wird **einmal** gesetzt: Ausführungspreis der
   Einstiegs-Order (ohne Kommission), bei bereits gehaltenen Aktien der `avgPrice` der Position.
10. Order-Abgleich über exakte Float-Preise → Abgleich über gespeicherte Order-ID und Preis ±0.01.
11. Hart codierte API-Keys → alles aus `.env`.
12. Veraltetes `openai.ChatCompletion` → Anthropic Python SDK.
13. Beim Entfernen blieben Orders offen → Nachfrage, ob offene Orders storniert werden sollen.

## Tests

```bat
.venv\Scripts\activate
pytest
```

Die Tests laufen ohne Netzwerk und ohne Tkinter (Fake-Broker, gemockte IBKR-Session, gemockter Claude-Client)
und in GitHub Actions auf Python 3.11 und 3.12.

## Lizenz

MIT – siehe [LICENSE](LICENSE). Nichts in diesem Projekt ist Anlageberatung.
