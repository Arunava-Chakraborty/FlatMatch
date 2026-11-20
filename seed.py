"""Create demo profiles. Usage: python seed.py  (all demo passwords: demo12345)
Uses DATABASE_URL (Postgres) if set, otherwise the local SQLite file."""
import time
from app.main import db, hash_pw, init_db, close_db, IS_PG

PEOPLE = [
  ("Priya", 25, "female", "Pune", "Baner", 9000, 14000, "roommate", "UX Designer", "2BHK near Balewadi High Street. Looking for a tidy, working flatmate.", 0, 0, 0, 1, 5, "early", "rarely", "any"),
  ("Rohan", 27, "male", "Pune", "Hinjewadi Ph 1", 8000, 12000, "room", "Data Engineer", "WFH 3 days a week. Gym in the morning, Netflix at night.", 0, 1, 1, 0, 4, "early", "sometimes", "any"),
  ("Sneha", 24, "female", "Pune", "Kharadi", 10000, 16000, "room", "Analyst", "Relocating from Kolkata, need a room by next month.", 0, 0, 0, 1, 4, "flexible", "rarely", "female"),
  ("Arjun", 29, "male", "Hyderabad", "Gachibowli", 12000, 20000, "roommate", "Product Manager", "3BHK gated society, one room free. Pet friendly.", 0, 1, 1, 0, 3, "late", "often", "any"),
  ("Meera", 26, "female", "Hyderabad", "Madhapur", 9000, 15000, "room", "Cloud Engineer", "Quiet, love cooking, looking for a calm space.", 0, 0, 0, 1, 5, "early", "rarely", "any"),
  ("Kabir", 23, "male", "Bangalore", "HSR Layout", 7000, 11000, "room", "SDE Intern", "First job! Easy going, keep things clean.", 0, 0, 1, 0, 3, "late", "sometimes", "any"),
]

def seed():
    init_db()
    with db() as c:
        for p in PEOPLE:
            h, s = hash_pw("demo12345")
            c.execute("""INSERT INTO users(email,pw_hash,salt,name,age,gender,city,area,budget_min,budget_max,move_in,
              looking,occupation,bio,smoking,pets,drinks,veg,cleanliness,sleep,guests,pref_gender,created)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(email) DO NOTHING""",
              (f"{p[0].lower()}@demo.flatmatch", h, s, p[0], p[1], p[2], p[3], p[4], p[5], p[6], "2026-12-15",
               p[7], p[8], p[9], p[10], p[11], p[12], p[13], p[14], p[15], p[16], p[17], time.time()))
    close_db()
    print(f"Seeded {len(PEOPLE)} demo users into {'Postgres' if IS_PG else 'SQLite'} (password: demo12345)")

if __name__ == "__main__":
    seed()
