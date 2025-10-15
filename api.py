from bridge import bridge
from printer import printer
from thermal import thermal
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from waitress import serve

app = Flask(__name__)

CORS(app)


@app.route("/print", methods=["POST"])
def handle_print():
    if "file" not in request.files:
        error_msg = "'file' field is required."
        bridge.add_log.emit(f"Print error: {error_msg}")
        return jsonify({"ok": False, "message": error_msg}), 400

    try:
        image_file = request.files["file"]
        try:
            printer.print_file(image_file)
            return jsonify({"ok": True, "message": "Print job sent."})
        except Exception as e:
            return jsonify({"ok": False, "message": str(e)}), 500

    except Exception as e:
        error_msg = str(e)
        bridge.add_log.emit(f"Print error: {error_msg}")
        return jsonify({"ok": False, "message": error_msg}), 500


@app.route("/thermal", methods=["POST"])
def handle_thermal():
    url = request.form.get("url") or request.args.get("url")
    code = request.form.get("code") or request.args.get("code")
    
    if not url or not code:
        return jsonify({"ok": False, "message": "'url' and 'code' are required"}), 400

    try:
        # Get mode from GUI settings (no more manual mode parameter needed)
        mode = thermal.get_thermal_mode()
        
        if mode.upper() == "USB":
            thermal.print_thermal_usb(url, code)
        else:
            thermal.print_thermal_ble(url, code)
    except Exception as e:
        return jsonify({"ok": False, "message": f"{str(e)}"}), 500

    return jsonify({"ok": True, "message": f"Thermal job sent via {mode}."})


@app.route("/thermal/ble", methods=["POST"])
def handle_thermal_ble():
    url = request.form.get("url") or request.args.get("url")
    code = request.form.get("code") or request.args.get("code")
    
    if not url or not code:
        return jsonify({"ok": False, "message": "'url' and 'code' are required"}), 400

    try:
        thermal.print_thermal_ble(url, code)
    except Exception as e:
        return jsonify({"ok": False, "message": f"{str(e)}"}), 500

    return jsonify({"ok": True, "message": "BLE thermal job sent."})


@app.route("/thermal/usb", methods=["POST"])
def handle_thermal_usb():
    url = request.form.get("url") or request.args.get("url")
    code = request.form.get("code") or request.args.get("code")
    
    if not url or not code:
        return jsonify({"ok": False, "message": "'url' and 'code' are required"}), 400

    try:
        thermal.print_thermal_usb(url, code)
    except Exception as e:
        return jsonify({"ok": False, "message": f"{str(e)}"}), 500

    return jsonify({"ok": True, "message": "USB thermal job sent."})


def run_flask():
    serve(app, host="0.0.0.0", port=2462, threads=4)
