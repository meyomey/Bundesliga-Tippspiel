"""PWA-spezifische Routes."""
from flask import Blueprint, render_template, send_from_directory, jsonify, current_app
import os

pwa_bp = Blueprint("pwa", __name__)


@pwa_bp.route("/offline")
def offline():
    return render_template("offline.html"), 200


@pwa_bp.route("/api/ping")
def ping():
    return jsonify({"ok": True}), 200


@pwa_bp.route("/sw.js")
def service_worker():
    resp = send_from_directory(
        os.path.join(current_app.root_path, "static", "js"),
        "sw.js",
    )
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Content-Type"] = "application/javascript"
    return resp


def register_pwa_routes(app):
    app.register_blueprint(pwa_bp)
