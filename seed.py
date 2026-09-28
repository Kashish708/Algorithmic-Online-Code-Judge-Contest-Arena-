import datetime
from database import SessionLocal
import models

def seed_data():
    db = SessionLocal()
    try:
        # Check if database is already populated
        if db.query(models.Problem).count() > 0:
            return

        print("Seeding initial problems and contest...")

        # 1. Seed Contest #1
        now = datetime.datetime.utcnow()
        contest = models.Contest(
            title="Weekly Contest 1",
            start_time=now - datetime.timedelta(hours=1),
            end_time=now + datetime.timedelta(hours=3),
            freeze_time=now + datetime.timedelta(hours=2)
        )
        db.add(contest)
        db.commit()

        # 2. Seed Problems
        problems = [
            {
                "title": "Sum Two Numbers",
                "description": "Read two integers and print their sum.",
                "time_limit": 2.0,
                "memory_limit_mb": 256.0,
                "checker_type": "exact",
                "test_cases": [
                    {"input_data": "5 10", "expected_output": "15", "weight": 50, "is_hidden": False},
                    {"input_data": "40 60", "expected_output": "100", "weight": 50, "is_hidden": True},
                ]
            },
            {
                "title": "Find the Maximum",
                "description": "Given two integers separated by a space, print the larger of the two.",
                "time_limit": 1.0,
                "memory_limit_mb": 256.0,
                "checker_type": "exact",
                "test_cases": [
                    {"input_data": "10 20", "expected_output": "20", "weight": 30, "is_hidden": False},
                    {"input_data": "-5 3", "expected_output": "3", "weight": 70, "is_hidden": True},
                ]
            },
            {
                "title": "Palindrome Checker",
                "description": "Given a single word (lowercase English letters), print YES if it is a palindrome and NO otherwise.",
                "time_limit": 1.0,
                "memory_limit_mb": 256.0,
                "checker_type": "exact",
                "test_cases": [
                    {"input_data": "racecar", "expected_output": "YES", "weight": 50, "is_hidden": False},
                    {"input_data": "onlinejudge", "expected_output": "NO", "weight": 50, "is_hidden": True},
                ]
            },
            {
                "title": "Array Sum",
                "description": "The first line contains an integer N (the size of the array). The second line contains N space-separated integers. Print the total sum of the array.",
                "time_limit": 2.0,
                "memory_limit_mb": 256.0,
                "checker_type": "exact",
                "test_cases": [
                    {"input_data": "5\n1 2 3 4 5", "expected_output": "15", "weight": 40, "is_hidden": False},
                    {"input_data": "3\n-10 20 -5", "expected_output": "5", "weight": 60, "is_hidden": True},
                ]
            },
            {
                "title": "N-th Fibonacci Number",
                "description": "Given an integer N, print the N-th number in the Fibonacci sequence. Assume F(0) = 0 and F(1) = 1.",
                "time_limit": 1.5,
                "memory_limit_mb": 256.0,
                "checker_type": "exact",
                "test_cases": [
                    {"input_data": "10", "expected_output": "55", "weight": 40, "is_hidden": False},
                    {"input_data": "20", "expected_output": "6765", "weight": 60, "is_hidden": True},
                ]
            }
        ]

        for p in problems:
            prob = models.Problem(
                title=p["title"],
                description=p["description"],
                time_limit=p["time_limit"],
                memory_limit_mb=p["memory_limit_mb"],
                checker_type=p["checker_type"]
            )
            db.add(prob)
            db.commit()
            db.refresh(prob)

            for tc in p["test_cases"]:
                db.add(models.TestCase(
                    problem_id=prob.id,
                    input_data=tc["input_data"],
                    expected_output=tc["expected_output"],
                    weight=tc["weight"],
                    is_hidden=tc["is_hidden"]
                ))
            db.commit()

        print("Database successfully seeded.")
    finally:
        db.close()

if __name__ == "__main__":
    seed_data()