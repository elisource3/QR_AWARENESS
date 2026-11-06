import os
import hmac
import secrets
from pathlib import Path
import time

from flask import (
    Flask,
    abort,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import update


# --------------------------------------------------
# CONFIGURAZIONE
# --------------------------------------------------

app = Flask(__name__)

production = os.getenv("APP_ENV", "development").lower() == "production"

secret_key = os.getenv("SECRET_KEY")

if production and not secret_key:
    raise RuntimeError("SECRET_KEY non configurata nell'ambiente di produzione.")

app.config["SECRET_KEY"] = secret_key or secrets.token_hex(32)

cookie_secure = os.getenv(
    "COOKIE_SECURE",
    "true" if production else "false"
).lower() == "true"

if production and not cookie_secure:
    raise RuntimeError("COOKIE_SECURE deve essere true in produzione.")

app.config.update(
    SESSION_COOKIE_NAME="qr_session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=cookie_secure,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,
    MAX_CONTENT_LENGTH=16 * 1024,
)


# --------------------------------------------------
# DATABASE
# --------------------------------------------------

data_dir = Path(os.getenv(
    "DATA_DIR",
    str(Path(__file__).resolve().parent / "data")
)).resolve()

data_dir.mkdir(parents=True, exist_ok=True)

database_url = os.getenv("DATABASE_URL")

if not database_url:
    sqlite_path = (data_dir / "qr_awareness.db").resolve()
    database_url = f"sqlite:///{sqlite_path.as_posix()}"

elif database_url.startswith("postgres://"):
    database_url = database_url.replace(
        "postgres://",
        "postgresql+psycopg://",
        1
    )

elif database_url.startswith("postgresql://"):
    database_url = database_url.replace(
        "postgresql://",
        "postgresql+psycopg://",
        1
    )

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

if database_url.startswith("sqlite:"):
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "connect_args": {"check_same_thread": False}
    }

db = SQLAlchemy(app)


# --------------------------------------------------
# CONTATORI
# --------------------------------------------------

class Counter(db.Model):
    name = db.Column(db.String(40), primary_key=True)
    value = db.Column(db.Integer, nullable=False, default=0)


EVENTS = (
    "landing_opens",
    "return_visits",
    "link_clicks",
    "awareness_opens",
)


def initialize_counters():
    """Crea le tabelle e inizializza solo i contatori mancanti."""

    db.create_all()

    dialect = db.engine.dialect.name

    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise RuntimeError("Tipo di database non supportato.")

    for event_name in EVENTS:
        statement = (
            insert(Counter)
            .values(name=event_name, value=0)
            .on_conflict_do_nothing(index_elements=["name"])
        )
        db.session.execute(statement)

    db.session.commit()


def increment_counter(name):
    """Incrementa atomicamente un contatore già inizializzato."""

    result = db.session.execute(
        update(Counter)
        .where(Counter.name == name)
        .values(value=Counter.value + 1)
    )

    if result.rowcount != 1:
        db.session.rollback()
        raise RuntimeError(f"Contatore non trovato: {name}")

    db.session.commit()



def register_landing_visit():
    """Registra una visita di ritorno dopo 30 minuti di inattività."""

    if session.pop("return_visit_pending", False):
        increment_counter("return_visits")



with app.app_context():
    initialize_counters()


# --------------------------------------------------
# SESSIONE E PROTEZIONE CSRF
# --------------------------------------------------


@app.before_request
def manage_session():
    """Gestisce la sessione e rileva 30 minuti di inattività."""

    now = int(time.time())
    last_activity = session.get("last_activity_at")
    app.logger.info(
        "SESSION DEBUG | path=%s | last=%s | now=%s | idle=%s | qr=%s | pending=%s",
        request.path,
        last_activity,
        now,
        now - last_activity if last_activity is not None else None,
        request.cookies.get("qr_visited"),
        session.get("return_visit_pending")
    )

    if (
        last_activity is not None
        and now - last_activity >= 1800
    ):
        # Inizia una nuova sessione logica.
        session.pop("link_clicked", None)
        session.pop("awareness_counted", None)

        # Se il browser è già stato riconosciuto,
        # prepara il conteggio della visita di ritorno.
        if request.cookies.get("qr_visited") == "1":
            session["return_visit_pending"] = True

    session.permanent = True



def get_csrf_token():
    """Restituisce un token CSRF associato alla sessione."""

    token = session.get("_csrf_token")

    if token is None:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token

    return token


@app.context_processor
def inject_csrf_token():
    return {"csrf_token": get_csrf_token}


def validate_csrf_token():
    """Verifica il token CSRF inviato dal modulo."""

    submitted_token = request.form.get("csrf_token", "")
    expected_token = session.get("_csrf_token", "")

    if not expected_token or not hmac.compare_digest(
        submitted_token,
        expected_token
    ):
        abort(400)


# --------------------------------------------------
# INTESTAZIONI HTTP DI SICUREZZA
# --------------------------------------------------

@app.after_request
def add_security_headers(response):

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"

    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=()"
    )

    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "img-src 'self'; "
        "style-src 'self'; "
        "script-src 'self'; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    )

    if production:
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000"
        )

    session["last_activity_at"] = int(time.time())

    return response


# --------------------------------------------------
# ROUTE PRINCIPALI
# --------------------------------------------------

@app.get("/qr")
def qr_entry():

    response = redirect(url_for("index"), code=302)

    # Evita di riutilizzare una risposta memorizzata in cache.
    response.headers["Cache-Control"] = "no-store"

    # Conta solo la prima visita riconosciuta da questo browser.
    if request.cookies.get("qr_visited") != "1":

        increment_counter("landing_opens")

        response.set_cookie(
            "qr_visited",
            "1",
            max_age=60 * 60 * 24 * 365,
            path="/",
            secure=cookie_secure,
            httponly=True,
            samesite="Lax",
        )

    return response


@app.get("/")
def index():
    register_landing_visit()
    return render_template("index.html")


@app.post("/go")
def go_to_awareness():

    validate_csrf_token()

    # Conta al massimo un clic per sessione.
    if not session.get("link_clicked"):

        increment_counter("link_clicks")
        session["link_clicked"] = True

    return redirect(url_for("awareness"), code=303)


@app.get("/awareness")
def awareness():

    # Conta solo gli accessi successivi al clic sul pulsante.
    # I refresh della pagina non incrementano nuovamente il contatore.
    if (
        session.get("link_clicked")
        and not session.get("awareness_counted")
    ):
        increment_counter("awareness_opens")
        session["awareness_counted"] = True

    return render_template("awareness.html")


@app.get("/health")
def health():
    return {"status": "ok"}


# --------------------------------------------------
# AVVIO LOCALE
# --------------------------------------------------

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=False
    )
