# Follow-Me Drone — Setup

Baut auf deinem bisherigen Stand auf: Pi Zero 2 W, `pymavlink`-Umgebung
`~/followenv`, Verkabelung Pi ↔ SpeedyBee F405 V5 über UART4/SERIAL4
(115200 8N1, MAVLink2) — das bleibt alles unverändert.

Neu dazugekommen: Bluetooth-Start vom Laptop, GPS vom iPhone über WLAN,
Mission-Planner-Workflow.

## Architektur

```
LAPTOP (Chrome)                    IPHONE (Safari)
control.html                       phone_gps.html
   |  Bluetooth LE                    |  HTTPS/WLAN
   |  Start / Stop                    |  GPS-Position
   v                                  v
+---------------------------------------------------+
|                  RASPBERRY PI ZERO 2 W              |
|  ble_control.py        gps_server.py                |
|         \                  /                        |
|          \                /                         |
|           follow_me.py (Hauptschleife)               |
|                    |                                 |
|            drone_controller.py                       |
|                    |  /dev/serial0 (UART4)            |
+--------------------|---------------------------------+
                      v
              SpeedyBee F405 V5 (ArduPilot)
                      ^
                      | USB / COM5 — NUR VOR DEM FLUG
                      |
              Mission Planner (Windows-PC)
```

Mission Planner bleibt bei USB/COM5 wie bisher und wird **vor** dem Flug
für Kalibrierung und Pre-Arm-Check benutzt, dann geschlossen bzw.
getrennt. Während des Fluges hat der Pi die alleinige Verbindung über
SERIAL4 — kein Risiko, dass zwei Systeme gleichzeitig Befehle senden.

## 1. Dateien auf den Pi kopieren

Alle Dateien aus diesem Ordner nach `~/followme_drone/` auf dem Pi
kopieren (z.B. mit `scp` von deinem Laptop aus):

```
scp -r followme_drone pimaster@raspOnBoard.local:~/
```

## 2. Zusätzliche Pakete installieren

```
ssh pimaster@raspOnBoard.local
source ~/followenv/bin/activate
sudo apt-get update
sudo apt-get install -y bluetooth bluez python3-dbus
pip install bluezero
```

(`pymavlink`, `pyserial` etc. hast du laut deinem Setup-Dokument schon.)

## 3. Bluetooth auf dem Pi aktivieren & sichtbar machen

```
sudo systemctl enable bluetooth
sudo systemctl start bluetooth
sudo hciconfig hci0 piscan
```

## 4. Self-signed HTTPS-Zertifikat erzeugen (fürs iPhone-GPS)

Einmalig, im Ordner `~/followme_drone`:

```
openssl req -x509 -newkey rsa:2048 -nodes \
    -keyout key.pem -out cert.pem -days 365 -subj "/CN=raspOnBoard"
```

## 5. Testlauf (manuell, wie bisher gewohnt)

```
source ~/followenv/bin/activate
cd ~/followme_drone
python3 follow_me.py
```

Das Skript verbindet sich mit dem Flight Controller, startet den
Bluetooth- und den GPS-Server, und wartet dann.

## 6. iPhone verbinden

1. Pi und iPhone müssen im **gleichen WLAN** sein (oder du nutzt den
   Pi als eigenen Hotspot — dafür separat fragen, das ist ein
   zusätzlicher Schritt).
2. `phone_gps.html` auf den Pi kopieren und im Safari öffnen — am
   einfachsten, indem du sie dir selbst per AirDrop/Mail/iCloud aufs
   iPhone schickst und dort öffnest (sie braucht keinen eigenen Server,
   läuft als lokale Datei).
3. Beim ersten Verbindungsversuch zeigt Safari eine
   Zertifikatswarnung (weil das Zertifikat selbstsigniert ist) —
   **einmal** `https://raspOnBoard.local:5000` direkt im Safari
   aufrufen, "Details einblenden" → "Diese Website besuchen" bestätigen.
   Danach funktioniert `phone_gps.html` normal.
4. In `phone_gps.html` die Pi-Adresse eintragen (Standard:
   `raspOnBoard.local`), "Standort senden starten" tippen, GPS-Zugriff
   erlauben.

## 7. Laptop verbinden

1. `control.html` im Chrome oder Edge öffnen (lokale Datei reicht,
   z.B. per Doppelklick).
2. "Mit Drohne verbinden" klicken, "FollowMeDrone" in der Geräteliste
   auswählen.
3. Sobald verbunden: "Start" arm't die Drohne und beginnt die
   Follow-Me-Schleife. "Stop / RTL" löst jederzeit einen kontrollierten
   Rückflug aus.

## 8. Automatischer Start beim Booten (optional)

Damit `follow_me.py` nach jedem Einschalten des Pi automatisch läuft
und auf "Start" wartet, ohne dass du dich per SSH einloggen musst:

```
sudo cp followme.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable followme.service
sudo systemctl start followme.service
```

Prüfen mit `sudo journalctl -u followme.service -f`.

## Sicherheit — bitte unbedingt beachten

- **Erster Test immer in SITL**, dann am Boden mit Props ab, erst
  danach draußen fliegen — genau wie du es bisher schon gemacht hast.
- `MAX_RADIUS_FROM_HOME_M` in `config.py` ist ein einfaches
  Software-Geofence. Das ersetzt **nicht** ArduPilots eigenes
  Geofence — richte das zusätzlich in Mission Planner ein
  (Config → Fence).
- Wenn Handy-GPS oder Bluetooth-Verbindung wegfällt, löst das System
  automatisch RTL aus (`PHONE_LINK_TIMEOUT_S`,
  `MAVLINK_LINK_TIMEOUT_S` in `config.py`).
- Teste "Stop / RTL" vom Laptop aus **immer zuerst am Boden**, bevor
  du dich beim ersten echten Flug darauf verlässt.
- Die Bluetooth-Statusrückmeldung (`notify_status`) ist best-effort —
  je nach `bluezero`-Version kann die genaue API leicht abweichen.
  Falls die Statuszeile in `control.html` leer bleibt, ist das nicht
  sicherheitsrelevant (Start/Stop funktioniert trotzdem), sag mir
  aber gern die Fehlermeldung aus der Pi-Konsole, dann passe ich es an.

## Dateien in diesem Ordner

| Datei | Zweck |
|---|---|
| `config.py` | alle Einstellungen an einem Ort |
| `drone_controller.py` | MAVLink-Wrapper (dein bisheriger Code, bereinigt) |
| `geo_utils.py` | Distanz-/Zielpunkt-Berechnung |
| `gps_server.py` | HTTPS-Server, nimmt GPS vom iPhone entgegen |
| `ble_control.py` | Bluetooth-LE-Peripheriegerät, nimmt Start/Stop vom Laptop entgegen |
| `follow_me.py` | Hauptschleife — verbindet alles |
| `phone_gps.html` | Seite fürs iPhone (Safari) |
| `control.html` | Steuerseite fürs Laptop (Chrome/Edge) |
| `followme.service` | optionaler systemd-Autostart |
