from flask import Flask, render_template, request, jsonify
from mailbot import MailBot, load_config, save_config, encrypt_password, load_processed
import threading

app = Flask(__name__)

# Globaler Bot und Scan-Status
bot = None
scan_status = {
    "scanning": False,
    "progress": 0,
    "message": "",
    "newsletters": []
}
unsubscribe_status = {
    "running": False,
    "current": 0,
    "total": 0,
    "results": []
}


@app.route("/")
def index():
    """Hauptseite."""
    config = load_config()
    # Passwort nicht an Frontend senden
    config["password"] = "********" if config.get("password") else ""
    return render_template("index.html", config=config)


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    """Konfiguration abrufen oder speichern."""
    if request.method == "GET":
        config = load_config()
        config["password"] = "********" if config.get("password") else ""
        return jsonify(config)

    elif request.method == "POST":
        data = request.json
        config = load_config()

        if data.get("email"):
            config["email"] = data["email"]

        if data.get("password") and data["password"] != "********":
            config["password"] = encrypt_password(data["password"])

        if data.get("imap_server"):
            config["imap_server"] = data["imap_server"]

        if data.get("imap_port"):
            config["imap_port"] = int(data["imap_port"])

        save_config(config)
        return jsonify({"success": True, "message": "Konfiguration gespeichert"})


@app.route("/api/test-connection", methods=["POST"])
def test_connection():
    """Testet die Verbindung zum E-Mail-Server."""
    global bot
    bot = MailBot()
    success, message = bot.test_connection()
    return jsonify({"success": success, "message": message})


@app.route("/api/scan", methods=["POST"])
def start_scan():
    """Startet den Newsletter-Scan."""
    global bot, scan_status

    if scan_status["scanning"]:
        return jsonify({"success": False, "message": "Scan läuft bereits"})

    def do_scan():
        global bot, scan_status
        scan_status["scanning"] = True
        scan_status["progress"] = 0
        scan_status["message"] = "Verbinde..."
        scan_status["newsletters"] = []

        try:
            bot = MailBot()
            success, message = bot.connect()

            if not success:
                scan_status["message"] = message
                scan_status["scanning"] = False
                return

            scan_status["progress"] = 20
            scan_status["message"] = "Scanne Posteingang..."

            newsletters = bot.scan_all(limit_per_folder=200)

            scan_status["progress"] = 90
            scan_status["message"] = f"{len(newsletters)} Newsletter gefunden"
            scan_status["newsletters"] = newsletters

            bot.disconnect()

            scan_status["progress"] = 100
            scan_status["scanning"] = False

        except Exception as e:
            scan_status["message"] = f"Fehler: {str(e)}"
            scan_status["scanning"] = False

    thread = threading.Thread(target=do_scan)
    thread.start()

    return jsonify({"success": True, "message": "Scan gestartet"})


@app.route("/api/scan/status")
def scan_status_api():
    """Gibt den aktuellen Scan-Status zurück."""
    return jsonify(scan_status)


@app.route("/api/newsletters")
def get_newsletters():
    """Gibt die gefundenen Newsletter zurück."""
    processed = load_processed()
    newsletters = scan_status.get("newsletters", [])

    # Status aktualisieren
    for nl in newsletters:
        nl["processed"] = nl["id"] in processed.get("processed_ids", [])
        nl["unsubscribed"] = nl["id"] in processed.get("unsubscribed", [])

    return jsonify(newsletters)


@app.route("/api/unsubscribe", methods=["POST"])
def unsubscribe():
    """Meldet von ausgewählten Newslettern ab."""
    global bot, unsubscribe_status

    if unsubscribe_status["running"]:
        return jsonify({"success": False, "message": "Abmeldung läuft bereits"})

    data = request.json
    newsletter_ids = data.get("ids", [])

    if not newsletter_ids:
        return jsonify({"success": False, "message": "Keine Newsletter ausgewählt"})

    newsletters_to_unsubscribe = [
        nl for nl in scan_status.get("newsletters", [])
        if nl["id"] in newsletter_ids
    ]

    def do_unsubscribe():
        global bot, unsubscribe_status
        unsubscribe_status["running"] = True
        unsubscribe_status["current"] = 0
        unsubscribe_status["total"] = len(newsletters_to_unsubscribe)
        unsubscribe_status["results"] = []

        try:
            bot = MailBot()

            for i, nl in enumerate(newsletters_to_unsubscribe):
                unsubscribe_status["current"] = i + 1

                results = bot.unsubscribe(nl)

                status = "error"
                message = "Keine Abmelde-Links gefunden"

                if results:
                    if any(r["status"] == "success" for r in results):
                        status = "success"
                        message = "Erfolgreich abgemeldet"
                    elif any(r["status"] == "needs_confirmation" for r in results):
                        status = "needs_confirmation"
                        message = "Manuelle Bestätigung erforderlich"
                        # Link für manuelle Bestätigung hinzufügen
                        for r in results:
                            if r["status"] == "needs_confirmation":
                                message += f" - {r['link']}"
                                break
                    else:
                        message = results[0].get("message", "Fehler")

                unsubscribe_status["results"].append({
                    "newsletter": nl["from"],
                    "status": status,
                    "message": message
                })

        except Exception as e:
            unsubscribe_status["results"].append({
                "newsletter": "System",
                "status": "error",
                "message": str(e)
            })
        finally:
            unsubscribe_status["running"] = False

    thread = threading.Thread(target=do_unsubscribe)
    thread.start()

    return jsonify({"success": True, "message": "Abmeldung gestartet"})


@app.route("/api/unsubscribe/status")
def unsubscribe_status_api():
    """Gibt den aktuellen Abmelde-Status zurück."""
    return jsonify(unsubscribe_status)


if __name__ == "__main__":
    print("=" * 50)
    print("iCloud Newsletter Abmelde-Bot")
    print("=" * 50)
    print("\nStarte Web-Server...")
    print("Öffne http://localhost:5000 in deinem Browser\n")
    app.run(host="127.0.0.1", port=5000, debug=True)
