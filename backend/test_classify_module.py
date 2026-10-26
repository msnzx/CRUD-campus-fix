from app.config import get_settings
from app.auth import admin_client
from app.classify import classify

settings = get_settings()
db = admin_client(settings)

ticket = {
    "ticket_id": 999,
    "title": "Bathroom sink leaking",
    "description": "Water is leaking badly from the sink."
}

print(classify(ticket, db, settings))