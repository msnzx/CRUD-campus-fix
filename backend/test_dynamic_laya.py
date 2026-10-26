from laya import Router
from app.config import get_settings
from app.auth import admin_client

db = admin_client(get_settings())

issues = db.table("issue_types").select("*").execute().data
departments = db.table("departments").select("*").execute().data

issue_lookup = {x["name"]: x for x in issues}
dept_lookup = {x["department_id"]: x["name"] for x in departments}

questions = {
    "issue_type": {
        "type": "choice",
        "instructions": "Choose the best campus issue category.",
        "criteria": {
            x["name"]: x["description"] for x in issues
        }
    },
    "priority": {
        "type": "choice",
        "instructions": "Determine urgency.",
        "criteria": {
            "LOW": "minor or cosmetic",
            "MEDIUM": "normal request",
            "HIGH": "serious or worsening",
            "URGENT": "immediate danger, flooding, fire, major outage"
        }
    }
}

router = Router()

result = router.predict(
    "The bathroom sink is leaking badly.",
    questions
)

issue_name = result["answers"]["issue_type"]["choice"]
issue = issue_lookup[issue_name]
department = dept_lookup[issue["default_department_id"]]

print("Issue:", issue_name)
print("Department:", department)
print("Priority:", result["answers"]["priority"]["choice"])
print(result)