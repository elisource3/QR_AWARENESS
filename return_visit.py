import time
from app import app, db, Counter

client = app.test_client()

# Simula un browser già riconosciuto.
client.set_cookie("qr_visited", "1")

# Simula una sessione inattiva da 31 minuti.
with client.session_transaction() as sess:
    sess["last_activity_at"] = int(time.time()) - 1860
    sess["link_clicked"] = True
    sess["awareness_counted"] = True

# Simula il ritorno tramite il QR.
response = client.get("/qr", follow_redirects=True)

print("Status HTTP:", response.status_code)

with app.app_context():
    for counter in Counter.query.order_by(Counter.name).all():
        print(counter.name, counter.value)