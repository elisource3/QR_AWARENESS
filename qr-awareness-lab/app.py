
import os
from pathlib import Path

from flask import Flask, redirect, render_template, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import update

app = Flask(__name__)

# In locale useremo SQLite.
# Online imposteremo DATABASE_URL su un database PostgreSQL.
data_dir = Path(os.getenv("DATA_DIR", "/app/data"))
data_dir.mkdir(parents=True, exist_ok=True)

database_url = os.getenv("DATABASE_URL")

if not database_url:
    database_url = f"sqlite:///{data_dir / 'qr_awareness.db'}"
elif database_url.startswith("postgres://"):
    database_url = database_url.replace(
        "postgres://", "postgresql+psycopg://", 1
    )
elif database_url.startswith("postgresql://"):
    database_url = database_url.replace(
        "postgresql://", "postgresql+psycopg://", 1
    )

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

if database_url.startswith("sqlite:"):
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "connect_args": {"check_same_thread": False}
    }

db = SQLAlchemy(app)

EVENTS = (
    "landing_opens",
    "awareness_opens",
    "link_clicks",
)


class Counter(db.Model):
    name = db.Column(db.String(40), primary_key=True)
    value = db.Column(db.Integer, nullable=False, default=0)


def increment_counter(name):
    # Incremento atomico: evita di sovrascrivere il valore
    # quando arrivano più richieste contemporaneamente.
    result = db.session.execute(
        update(Counter)
        .where(Counter.name == name)
        .values(value=Counter.value + 1)
    )

    if result.rowcount == 0:
        # Caso di recupero se il contatore non è ancora presente.
        db.session.add(Counter(name=name, value=1))

    db.session.commit()


with app.app_context():
    db.create_all()

    for event_name in EVENTS:
        if db.session.get(Counter, event_name) is None:
            db.session.add(Counter(name=event_name, value=0))

    db.session.commit()


@app.get("/")
def index():
    increment_counter("landing_opens")
    return render_template("index.html")


@app.get("/go")
def go_to_awareness():
    # Il clic passa dal server prima del reindirizzamento.
    increment_counter("link_clicks")
    return redirect(url_for("awareness"), code=302)


@app.get("/awareness")
def awareness():
    increment_counter("awareness_opens")
    return render_template("awareness.html")


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=False,
    )
