"""Health and API endpoints."""

from flask import Blueprint, jsonify

api_bp = Blueprint("api", __name__)


@api_bp.route("/health", methods=["GET"])
@api_bp.route("/christmas/health", methods=["GET"])
def health_check():
    """Health check endpoint for monitoring and reverse proxy validation."""
    return jsonify({"status": "ok", "app": "christmas_gift_exchange"}), 200
