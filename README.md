# Follow-Me-Drohne – Projektdateien

Dieses Repository dokumentiert die Software und ausgewählte Arbeitsnachweise meiner Maturaarbeit zur Entwicklung einer GPS-basierten Follow-Me-Drohne.

## Projektstatus

Die selbstgebaute Drohne ist flugfähig und manuell steuerbar. Die Follow-Me-Software wurde auf dem Laptop getestet und auf dem Raspberry Pi Zero 2 W eingerichtet. Die GPS-Übertragung vom iPhone zum Raspberry Pi sowie die Kommunikation des Raspberry Pi mit dem SpeedyBee F405 V5 über MAVLink wurden getestet.

Ein vollständiger autonomer Follow-Me-Flug wurde **nicht durchgeführt**, weil die Flugregelung der Drohne bis zum Abgabetermin noch nicht zuverlässig genug abgestimmt war. Dadurch wäre ein autonomer Test unnötig riskant und schwierig auszuwerten gewesen.

## Tatsächlich verwendete Architektur

```text
iPhone (Safari)
      |
      | HTTPS / WLAN
      | GPS-Position
      v
Raspberry Pi Zero 2 W
  ├─ gps_server.py
  ├─ phone_gps.html
  └─ follow_me.py
      |
      | UART / MAVLink
      v
SpeedyBee F405 V5
ArduPilot
      |
      v
Motorsteuerung / Flugregelung
```

Das iPhone stellt über `phone_gps.html` seine GPS-Daten bereit. `gps_server.py` nimmt diese Daten entgegen. `follow_me.py` filtert die GPS-Position, berechnet neue Zielpositionen und übergibt diese über MAVLink an ArduPilot. Die eigentliche Stabilisierung und Motorsteuerung übernimmt weiterhin der Flightcontroller.

**Bluetooth gehört nicht zum final verwendeten System.** Ein früherer Bluetooth-Entwurf ist nur zur Dokumentation des Entwicklungsprozesses unter `Docs/Entwicklungsstaende/Bluetooth_Entwurf/` abgelegt.

## Wichtige Dateien

| Datei | Zweck |
|---|---|
| `follow_me.py` | Hauptprogramm der Follow-Me-Logik |
| `gps_server.py` | HTTPS-Server für die GPS-Daten des iPhones |
| `phone_gps.html` | Browserseite für die GPS-Übertragung des iPhones |
| `drone_controller.py` | MAVLink-Kommunikation mit ArduPilot |
| `geo_utils.py` | Distanz- und Zielpunktberechnungen |
| `config.py` | Zentrale Konfigurationswerte |
| `local_test.py` | Lokaler Test der Follow-Me-Logik ohne echte Drohne |
| `fc_read_test.py` | Test zum Auslesen von Flightcontroller-Daten |

Weitere Dokumente, Videos und Prozessnachweise befinden sich im Ordner `Docs/`.

## Testmodus

In `config.py` ist `TEST_MODE` standardmässig auf `True` gesetzt. Dadurch kann die Follow-Me-Logik ohne echte Drohne getestet werden. In diesem Modus werden die GOTO-Befehle nur simuliert und im Terminal ausgegeben.

Für den Hardwarepfad muss `TEST_MODE = False` gesetzt werden. Dann verbindet sich `drone_controller.py` über `/dev/serial0` mit ArduPilot auf dem SpeedyBee. START und STOP werden beim manuellen Start im Terminal eingegeben, beispielsweise über SSH.

## GPS-Seite starten

Auf dem Raspberry Pi wird das Projekt in der Python-Umgebung `followenv` ausgeführt:

```bash
source ~/followenv/bin/activate
cd ~/followme_drone
python3 follow_me.py
```

`gps_server.py` stellt die GPS-Seite über HTTPS bereit. Das iPhone und der Raspberry Pi müssen sich im selben WLAN befinden. Die Seite kann danach im Browser über die Adresse des Raspberry Pi und Port 5000 geöffnet werden, beispielsweise:

```text
https://raspOnBoard.local:5000/
```

Für die Browser-Geolocation ist HTTPS notwendig. Das Projekt verwendete dafür ein lokales Zertifikat auf dem Raspberry Pi. Zertifikat und privater Schlüssel werden bewusst nicht im Repository veröffentlicht.

## Sicherheit

Die Software enthält Prüfungen für die Verbindung zum iPhone, die Aktualität der GPS-Daten, den MAVLink-Link und eine maximale Entfernung von der Home-Position. Bei entsprechenden Fehlerbedingungen wird RTL ausgelöst.

Zusätzlich wurde RC9 in ArduPilot direkt als LAND konfiguriert. Dieser Weg ist unabhängig vom Raspberry Pi und dient als Emergency Override.

Die Software wurde nicht für einen vollständigen autonomen Flug freigegeben, solange die Drohne ihre Position nicht zuverlässig halten konnte.

## Entwicklungsstände

Anstatt das alles auf dem Home Wlan läuft, ist der nächtse Schritt den Rasperry Pi als Hotspot zu nutzen das es überall verfügbar ist dann kann das Iphone sich mit dem Hotspot connecten und so die HTTPS seite öffnen und GPS-Signal senden.
