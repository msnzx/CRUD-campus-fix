from app.config import get_settings
from app.auth import admin_client

settings = get_settings()
db = admin_client(settings)

for table in ["departments", "issue_types", "priorities"]:
    rows = db.table(table).select("*").execute().data
    print(f"\n{table.upper()}")
    for row in rows:
        print(row)