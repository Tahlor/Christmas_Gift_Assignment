"""Flask Application Factory for Christmas Gift Exchange."""

import os
from flask import Flask, redirect, render_template, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from web.config import Config
from web.db import close_db, init_db
from web.routes.admin import admin_bp
from web.routes.api import api_bp
from web.routes.family import family_bp
from web.security import apply_security_headers, get_csrf_token, validate_csrf


def create_app(test_config=None) -> Flask:
    """Create and configure the Flask application."""
    app = Flask(
        __name__,
        static_url_path="/christmas/static",
        static_folder="static",
        template_folder="templates"
    )

    # Load configuration
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    # Validate production configuration
    if not app.config.get("TESTING"):
        Config.validate_production()

    # Initialize database
    db_path = app.config.get("DB_PATH")
    init_db(db_path)

    # Reverse proxy middleware support (Archimedes / Nginx)
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=1,
        x_proto=1,
        x_host=1,
        x_prefix=0  # Routes already include /christmas; avoid a duplicated proxy prefix
    )

    # Database connection teardown
    app.teardown_appcontext(close_db)

    # Security hooks
    app.before_request(validate_csrf)
    app.after_request(apply_security_headers)

    # Context processors
    @app.context_processor
    def inject_helpers():
        return {
            "csrf_token": get_csrf_token,
            "app_root": "/christmas"
        }

    # Root redirects to /christmas/
    @app.route("/", methods=["GET"])
    def root_redirect():
        return redirect("/christmas/")

    # Register blueprints
    app.register_blueprint(api_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(family_bp)

    # Error handlers
    @app.errorhandler(400)
    def bad_request(e):
        return render_template("error.html", code=400, title="Bad Request", message=str(e.description)), 400

    @app.errorhandler(404)
    def not_found(e):
        return render_template("error.html", code=404, title="Page Not Found", message=str(e.description)), 404

    @app.errorhandler(429)
    def rate_limited(e):
        return render_template("error.html", code=429, title="Too Many Requests", message=str(e.description)), 429

    @app.errorhandler(500)
    def internal_error(e):
        return render_template("error.html", code=500, title="Server Error", message="An unexpected error occurred. Please try again."), 500

    return app
