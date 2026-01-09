import imaplib
import email
from email.header import decode_header
import re
import json
import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from cryptography.fernet import Fernet
import base64
import hashlib

CONFIG_FILE = "config.json"
PROCESSED_FILE = "processed.json"
KEY_FILE = ".key"


def get_or_create_key():
    """Erstellt oder lädt den Verschlüsselungsschlüssel."""
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE, "rb") as f:
            return f.read()
    else:
        key = Fernet.generate_key()
        with open(KEY_FILE, "wb") as f:
            f.write(key)
        return key


def encrypt_password(password):
    """Verschlüsselt das Passwort."""
    key = get_or_create_key()
    f = Fernet(key)
    return f.encrypt(password.encode()).decode()


def decrypt_password(encrypted_password):
    """Entschlüsselt das Passwort."""
    key = get_or_create_key()
    f = Fernet(key)
    return f.decrypt(encrypted_password.encode()).decode()


def load_config():
    """Lädt die Konfiguration aus der JSON-Datei."""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {
        "email": "",
        "password": "",
        "imap_server": "imap.mail.me.com",
        "imap_port": 993
    }


def save_config(config):
    """Speichert die Konfiguration in die JSON-Datei."""
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def load_processed():
    """Lädt die Liste der bereits verarbeiteten Newsletter."""
    if os.path.exists(PROCESSED_FILE):
        with open(PROCESSED_FILE, "r") as f:
            return json.load(f)
    return {"processed_ids": [], "unsubscribed": []}


def save_processed(processed):
    """Speichert die Liste der verarbeiteten Newsletter."""
    with open(PROCESSED_FILE, "w") as f:
        json.dump(processed, f, indent=2)


def decode_mime_header(header_value):
    """Dekodiert MIME-kodierte Header (z.B. Betreff, Absender)."""
    if header_value is None:
        return ""

    decoded_parts = []
    for part, charset in decode_header(header_value):
        if isinstance(part, bytes):
            try:
                decoded_parts.append(part.decode(charset or "utf-8", errors="replace"))
            except:
                decoded_parts.append(part.decode("utf-8", errors="replace"))
        else:
            decoded_parts.append(part)
    return " ".join(decoded_parts)


def extract_email_address(from_header):
    """Extrahiert die E-Mail-Adresse aus dem From-Header."""
    match = re.search(r'<([^>]+)>', from_header)
    if match:
        return match.group(1)
    return from_header.strip()


def generate_message_id(msg):
    """Generiert eine eindeutige ID für eine Nachricht."""
    message_id = msg.get("Message-ID", "")
    if message_id:
        return hashlib.md5(message_id.encode()).hexdigest()

    from_addr = msg.get("From", "")
    date = msg.get("Date", "")
    subject = msg.get("Subject", "")
    combined = f"{from_addr}{date}{subject}"
    return hashlib.md5(combined.encode()).hexdigest()


class MailBot:
    def __init__(self):
        self.config = load_config()
        self.processed = load_processed()
        self.connection = None

    def connect(self):
        """Verbindet sich mit dem IMAP-Server."""
        try:
            self.connection = imaplib.IMAP4_SSL(
                self.config["imap_server"],
                self.config["imap_port"]
            )

            password = self.config["password"]
            if password.startswith("gAAAAA"):  # Verschlüsseltes Passwort
                password = decrypt_password(password)

            self.connection.login(self.config["email"], password)
            return True, "Verbindung erfolgreich!"
        except imaplib.IMAP4.error as e:
            return False, f"Anmeldefehler: {str(e)}"
        except Exception as e:
            return False, f"Verbindungsfehler: {str(e)}"

    def disconnect(self):
        """Trennt die Verbindung zum IMAP-Server."""
        if self.connection:
            try:
                self.connection.logout()
            except:
                pass
            self.connection = None

    def get_folders(self):
        """Gibt eine Liste der verfügbaren Ordner zurück."""
        if not self.connection:
            return []

        _, folders = self.connection.list()
        folder_names = []
        for folder in folders:
            match = re.search(r'"([^"]+)"$|(\S+)$', folder.decode())
            if match:
                name = match.group(1) or match.group(2)
                folder_names.append(name)
        return folder_names

    def scan_folder(self, folder_name, limit=None):
        """Scannt einen Ordner nach Newslettern."""
        newsletters = []

        try:
            # Ordnername für iCloud anpassen
            status, _ = self.connection.select(folder_name)
            if status != "OK":
                return newsletters

            # Alle Nachrichten holen
            _, message_numbers = self.connection.search(None, "ALL")
            message_ids = message_numbers[0].split()

            # Neueste zuerst, optional limitiert
            message_ids = message_ids[::-1]
            if limit:
                message_ids = message_ids[:limit]

            for num in message_ids:
                try:
                    _, msg_data = self.connection.fetch(num, "(RFC822.HEADER)")

                    for response_part in msg_data:
                        if isinstance(response_part, tuple):
                            msg = email.message_from_bytes(response_part[1])

                            # Prüfen ob Newsletter (List-Unsubscribe Header)
                            list_unsubscribe = msg.get("List-Unsubscribe")

                            if list_unsubscribe:
                                msg_id = generate_message_id(msg)
                                from_header = decode_mime_header(msg.get("From", ""))
                                subject = decode_mime_header(msg.get("Subject", ""))
                                date_str = msg.get("Date", "")

                                # Unsubscribe-Links extrahieren
                                unsubscribe_links = self._extract_unsubscribe_links(list_unsubscribe)

                                newsletter = {
                                    "id": msg_id,
                                    "from": from_header,
                                    "from_email": extract_email_address(from_header),
                                    "subject": subject,
                                    "date": date_str,
                                    "folder": folder_name,
                                    "unsubscribe_links": unsubscribe_links,
                                    "processed": msg_id in self.processed.get("processed_ids", []),
                                    "unsubscribed": msg_id in self.processed.get("unsubscribed", [])
                                }
                                newsletters.append(newsletter)
                except Exception as e:
                    continue

        except Exception as e:
            print(f"Fehler beim Scannen von {folder_name}: {e}")

        return newsletters

    def _extract_unsubscribe_links(self, header_value):
        """Extrahiert Abmelde-Links aus dem List-Unsubscribe Header."""
        links = {"http": [], "mailto": []}

        # HTTP/HTTPS Links
        http_matches = re.findall(r'<(https?://[^>]+)>', header_value)
        links["http"] = http_matches

        # Mailto Links
        mailto_matches = re.findall(r'<(mailto:[^>]+)>', header_value)
        links["mailto"] = mailto_matches

        return links

    def scan_all(self, limit_per_folder=None):
        """Scannt Posteingang und Spam nach Newslettern."""
        all_newsletters = []

        # iCloud Ordnernamen
        folders_to_scan = ["INBOX", "Junk"]

        for folder in folders_to_scan:
            newsletters = self.scan_folder(folder, limit_per_folder)
            all_newsletters.extend(newsletters)

        # Duplikate entfernen (basierend auf Absender-E-Mail)
        seen_senders = {}
        unique_newsletters = []

        for nl in all_newsletters:
            sender = nl["from_email"].lower()
            if sender not in seen_senders:
                seen_senders[sender] = True
                unique_newsletters.append(nl)

        return unique_newsletters

    def unsubscribe(self, newsletter):
        """Versucht, sich von einem Newsletter abzumelden."""
        results = []

        http_links = newsletter.get("unsubscribe_links", {}).get("http", [])

        for link in http_links:
            try:
                # Erst GET versuchen
                response = requests.get(
                    link,
                    timeout=10,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                    },
                    allow_redirects=True
                )

                if response.status_code == 200:
                    # Prüfen ob Bestätigung nötig
                    soup = BeautifulSoup(response.text, "html.parser")

                    # Nach Bestätigungs-Buttons/Forms suchen
                    forms = soup.find_all("form")
                    confirm_buttons = soup.find_all(
                        ["button", "input"],
                        text=re.compile(r"(confirm|unsubscribe|abmelden|bestätigen)", re.I)
                    )

                    if forms or confirm_buttons:
                        results.append({
                            "link": link,
                            "status": "needs_confirmation",
                            "message": "Manuelle Bestätigung auf der Website erforderlich"
                        })
                    else:
                        results.append({
                            "link": link,
                            "status": "success",
                            "message": "Erfolgreich abgemeldet"
                        })
                else:
                    results.append({
                        "link": link,
                        "status": "error",
                        "message": f"HTTP {response.status_code}"
                    })

            except requests.Timeout:
                results.append({
                    "link": link,
                    "status": "error",
                    "message": "Zeitüberschreitung"
                })
            except Exception as e:
                results.append({
                    "link": link,
                    "status": "error",
                    "message": str(e)
                })

        # Als verarbeitet markieren
        if newsletter["id"] not in self.processed["processed_ids"]:
            self.processed["processed_ids"].append(newsletter["id"])

        # Bei Erfolg als abgemeldet markieren
        if any(r["status"] == "success" for r in results):
            if newsletter["id"] not in self.processed["unsubscribed"]:
                self.processed["unsubscribed"].append(newsletter["id"])

        save_processed(self.processed)

        return results

    def test_connection(self):
        """Testet die Verbindung zum IMAP-Server."""
        success, message = self.connect()
        if success:
            self.disconnect()
        return success, message
