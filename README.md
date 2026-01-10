# iCloud Newsletter Abmelde-Bot

Ein Python-basierter Bot mit Web-Interface, der sich bei iCloud Mail einloggt, Newsletter in Posteingang und Spam identifiziert und automatisch abmeldet.

## Features

- Web-Interface zur einfachen Bedienung
- Automatische Newsletter-Erkennung via `List-Unsubscribe` Header
- Scan von Posteingang und Spam-Ordner
- Konfigurierbares Scan-Limit (alle E-Mails oder nur die neuesten X)
- Einzelne oder mehrere Newsletter gleichzeitig abmelden
- **Automatische Bestätigung** von Unsubscribe-Seiten (klickt Bestätigungsbuttons automatisch)
- Speicherung bereits verarbeiteter Newsletter
- Verschlüsselte Passwort-Speicherung
- Modernes, responsives Dark-Theme

## Voraussetzungen

- Python 3.8+
- iCloud E-Mail-Account
- App-spezifisches Passwort (nicht das normale iCloud-Passwort!)

## Installation

1. Repository klonen:
```bash
git clone git@github.com:iotstackcloud/newslettermailbot.git
cd newslettermailbot
```

2. Abhängigkeiten installieren:
```bash
pip install -r requirements.txt
```

3. Playwright für automatische Bestätigung installieren (optional, aber empfohlen):
```bash
python -m playwright install chromium
```

4. App-spezifisches Passwort erstellen:
   - Gehe zu https://appleid.apple.com
   - Anmelden → Sicherheit → App-spezifische Passwörter
   - Neues Passwort generieren

## Verwendung

1. Bot starten:
```bash
python app.py
```

2. Browser öffnen: http://localhost:5000

3. E-Mail und App-Passwort eingeben

4. "Verbindung testen" klicken

5. "Newsletter suchen" klicken

6. Newsletter auswählen und abmelden

## Projektstruktur

```
newslettermailbot/
├── app.py              # Flask Web-App
├── mailbot.py          # E-Mail-Logik (IMAP, Newsletter-Erkennung)
├── requirements.txt    # Python-Abhängigkeiten
├── templates/
│   └── index.html      # Web-Interface
├── static/
│   └── style.css       # Styling
└── .gitignore
```

## Sicherheit

- Das App-Passwort wird lokal verschlüsselt gespeichert
- `config.json` und `.key` sind in `.gitignore` enthalten
- Der Server läuft nur auf localhost (127.0.0.1)

## Hinweise

- Der Bot kann nur Newsletter abmelden, die den `List-Unsubscribe` Header haben
- Mit Playwright werden Bestätigungsbuttons auf Unsubscribe-Seiten automatisch geklickt
- Falls die automatische Bestätigung fehlschlägt (z.B. bei CAPTCHA), wird der Link zur manuellen Abmeldung angezeigt
- Ohne Playwright wird bei Bestätigungsseiten nur der Link angezeigt

## Lizenz

MIT License
