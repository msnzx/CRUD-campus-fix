from laya import Router

router = Router()

questions = {
    "department": {
        "type": "choice",
        "instructions": "Which campus department should handle this?",
        "criteria": {
            "Facilities": "plumbing, electrical, HVAC, furniture, buildings",
            "IT": "Wi-Fi, computers, accounts, classroom technology",
            "Residence Life": "housing and residence halls",
            "Student Engagement": "clubs and events",
            "Campus Safety": "ID, parking, safety",
            "Dining": "food, meal plans, swipes",
            "Finance": "financial issues",
            "Other / Review": "unclear cases"
        }
    },
    "issue_type": {
        "type": "choice",
        "instructions": "What kind of campus issue is this?",
        "criteria": ["Plumbing", "Electrical", "HVAC", "Technology", "Safety", "Other"]
    },
    "priority": {
        "type": "choice",
        "instructions": "How urgent is this issue?",
        "criteria": {
            "LOW": "cosmetic or minor inconvenience",
            "MEDIUM": "normal repair; no immediate danger",
            "HIGH": "serious issue, worsening damage, or user blocked",
            "URGENT": "immediate safety risk, flooding, fire, major outage"
        }
    }
}

result = router.predict(
    "The bathroom sink is leaking badly.",
    questions
)

print(result)