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
import time

# Playwright ist optional - wird nur für automatische Bestätigung benötigt
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

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
                                original_message_id = msg.get("Message-ID", "")
                                from_header = decode_mime_header(msg.get("From", ""))
                                subject = decode_mime_header(msg.get("Subject", ""))
                                date_str = msg.get("Date", "")

                                # Unsubscribe-Links extrahieren
                                unsubscribe_links = self._extract_unsubscribe_links(list_unsubscribe)

                                newsletter = {
                                    "id": msg_id,
                                    "message_id": original_message_id,
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

    def _auto_confirm_unsubscribe(self, url):
        """
        Verwendet Playwright um automatisch Bestätigungsbuttons auf Unsubscribe-Seiten zu klicken.
        Gibt (success, message) zurück.
        """
        if not PLAYWRIGHT_AVAILABLE:
            return False, "Playwright nicht installiert - führe 'pip install playwright && playwright install chromium' aus"

        # Typische Button/Link-Texte für Unsubscribe-Bestätigung (mehrsprachig)
        confirm_patterns = [
            # Deutsch
            r"abmelden", r"abbestellen", r"austragen", r"bestätigen", r"ja.*abmelden",
            r"newsletter.*abmelden", r"abmeldung.*bestätigen",
            # Englisch
            r"unsubscribe", r"confirm", r"yes.*unsubscribe", r"opt.?out", r"remove",
            r"stop.*emails?", r"cancel.*subscription",
            # Französisch
            r"désabonner", r"se désinscrire",
            # Spanisch
            r"cancelar.*suscripci", r"darse de baja",
        ]

        # Kombiniertes Pattern
        button_pattern = re.compile("|".join(confirm_patterns), re.IGNORECASE)

        try:
            with sync_playwright() as p:
                # Headless Browser starten
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )
                page = context.new_page()

                # Seite laden
                page.goto(url, timeout=15000, wait_until="domcontentloaded")
                time.sleep(1)  # Kurz warten für JavaScript

                # Prüfen ob bereits abgemeldet (Erfolgsmeldung auf der Seite)
                page_text = page.content().lower()
                success_indicators = [
                    "erfolgreich abgemeldet", "successfully unsubscribed",
                    "have been unsubscribed", "wurden abgemeldet",
                    "you are now unsubscribed", "abmeldung erfolgreich",
                    "subscription cancelled", "removed from", "opted out"
                ]

                for indicator in success_indicators:
                    if indicator in page_text:
                        browser.close()
                        return True, "Bereits abgemeldet (Erfolgsmeldung gefunden)"

                # Nach Bestätigungs-Buttons suchen
                clicked = False

                # Strategie 1: Buttons mit passendem Text
                buttons = page.locator("button, input[type='submit'], input[type='button']")
                count = buttons.count()

                for i in range(count):
                    try:
                        btn = buttons.nth(i)
                        btn_text = btn.inner_text() or btn.get_attribute("value") or ""
                        if button_pattern.search(btn_text):
                            btn.click(timeout=5000)
                            clicked = True
                            time.sleep(2)
                            break
                    except:
                        continue

                # Strategie 2: Links mit passendem Text
                if not clicked:
                    links = page.locator("a")
                    count = links.count()

                    for i in range(count):
                        try:
                            link = links.nth(i)
                            link_text = link.inner_text() or ""
                            if button_pattern.search(link_text):
                                link.click(timeout=5000)
                                clicked = True
                                time.sleep(2)
                                break
                        except:
                            continue

                # Strategie 3: Formulare submitten
                if not clicked:
                    forms = page.locator("form")
                    if forms.count() > 0:
                        # Erstes Formular mit Submit-Button finden
                        for i in range(forms.count()):
                            try:
                                form = forms.nth(i)
                                submit_btn = form.locator("button[type='submit'], input[type='submit']").first
                                if submit_btn.count() > 0:
                                    submit_btn.click(timeout=5000)
                                    clicked = True
                                    time.sleep(2)
                                    break
                            except:
                                continue

                # Nach dem Klick: Prüfen ob Erfolgsmeldung erscheint
                if clicked:
                    time.sleep(2)
                    page_text = page.content().lower()

                    for indicator in success_indicators:
                        if indicator in page_text:
                            browser.close()
                            return True, "Erfolgreich abgemeldet (automatisch bestätigt)"

                    # Auch ohne explizite Erfolgsmeldung als Erfolg werten
                    browser.close()
                    return True, "Bestätigung geklickt - wahrscheinlich abgemeldet"

                browser.close()
                return False, "Kein Bestätigungsbutton gefunden"

        except Exception as e:
            return False, f"Browser-Fehler: {str(e)}"

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

    def unsubscribe(self, newsletter, auto_confirm=True):
        """Versucht, sich von einem Newsletter abzumelden.

        Args:
            newsletter: Newsletter-Daten mit unsubscribe_links
            auto_confirm: Wenn True, wird versucht Bestätigungsseiten automatisch zu bestätigen
        """
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
                    page_text = response.text.lower()

                    # Prüfen ob bereits abgemeldet
                    success_indicators = [
                        "erfolgreich abgemeldet", "successfully unsubscribed",
                        "have been unsubscribed", "wurden abgemeldet",
                        "you are now unsubscribed", "abmeldung erfolgreich",
                        "subscription cancelled", "removed from", "opted out"
                    ]

                    already_unsubscribed = any(ind in page_text for ind in success_indicators)

                    if already_unsubscribed:
                        results.append({
                            "link": link,
                            "status": "success",
                            "message": "Erfolgreich abgemeldet"
                        })
                        continue

                    # Nach Bestätigungs-Buttons/Forms suchen
                    forms = soup.find_all("form")
                    confirm_buttons = soup.find_all(
                        ["button", "input"],
                        attrs={"type": ["submit", "button"]}
                    )
                    confirm_links = soup.find_all(
                        "a",
                        text=re.compile(r"(confirm|unsubscribe|abmelden|bestätigen|yes)", re.I)
                    )

                    needs_confirmation = bool(forms or confirm_buttons or confirm_links)

                    if needs_confirmation and auto_confirm:
                        # Automatische Bestätigung versuchen
                        success, message = self._auto_confirm_unsubscribe(link)

                        if success:
                            results.append({
                                "link": link,
                                "status": "success",
                                "message": message
                            })
                        else:
                            results.append({
                                "link": link,
                                "status": "needs_confirmation",
                                "message": f"Automatische Bestätigung fehlgeschlagen: {message}"
                            })
                    elif needs_confirmation:
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

    def delete_email(self, newsletter):
        """Löscht eine E-Mail aus dem Postfach.

        Args:
            newsletter: Newsletter-Daten mit message_id und folder

        Returns:
            (success, message) Tuple
        """
        message_id = newsletter.get("message_id")
        folder = newsletter.get("folder")

        if not message_id:
            return False, "Keine Message-ID vorhanden"

        if not self.connection:
            success, msg = self.connect()
            if not success:
                return False, f"Verbindungsfehler: {msg}"

        try:
            # Ordner auswählen
            status, _ = self.connection.select(folder)
            if status != "OK":
                return False, f"Ordner '{folder}' nicht gefunden"

            # Nach der Message-ID suchen
            # Message-ID muss in Anführungszeichen für IMAP-Suche
            search_id = message_id.strip("<>")
            _, message_numbers = self.connection.search(None, f'HEADER Message-ID "<{search_id}>"')

            if not message_numbers[0]:
                return False, "E-Mail nicht gefunden"

            # Alle gefundenen Nachrichten löschen (sollte nur eine sein)
            for num in message_numbers[0].split():
                # Als gelöscht markieren
                self.connection.store(num, '+FLAGS', '\\Deleted')

            # Gelöschte Nachrichten endgültig entfernen
            self.connection.expunge()

            return True, "E-Mail gelöscht"

        except Exception as e:
            return False, f"Löschfehler: {str(e)}"

    def test_connection(self):
        """Testet die Verbindung zum IMAP-Server."""
        success, message = self.connect()
        if success:
            self.disconnect()
        return success, message
