import uuid
import os
import secrets

from werkzeug.security import generate_password_hash, check_password_hash
from db import get_db_connection

DEMO_ADMIN_KEY = "DEMO-ADMIN"


def demo_mode_enabled():
    return os.getenv("DEMO_MODE", "true").strip().lower() in {"1", "true", "yes"}


def admin_registration_key():
    return DEMO_ADMIN_KEY if demo_mode_enabled() else os.getenv("ADMIN_SECRET_KEY", "")

def register_user(name, matric_no, password, department, security_answers=None):
    """
    Registers a new student account.
    security_answers must be a list of dicts: [{'question': ..., 'answer': ...}, ...]
    with exactly 10 entries.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    # Check if matric already exists among students only
    cursor.execute("SELECT * FROM users WHERE staff_id=%s AND role='student'", (matric_no,))
    if cursor.fetchone():
        conn.close()
        raise ValueError("Matric Number already exists!")

    hashed_pw = generate_password_hash(password)
    # Insert student account
    cursor.execute(
        "INSERT INTO users (name, department, staff_id, password, role) VALUES (%s, %s, %s, %s, %s)",
        (name, department, matric_no, hashed_pw, "student")
    )
    user_id = cursor.lastrowid

    # Save 10 security questions and answers
    if security_answers:
        for qa in security_answers:
            cursor.execute(
                "INSERT INTO security_questions (user_id, question, answer) VALUES (%s, %s, %s)",
                (user_id, qa['question'], generate_password_hash(qa['answer'].strip().lower()))
            )

    conn.commit()
    conn.close()

def register_admin(name, staff_id, password, secret_key):
    admin_key = admin_registration_key()
    if not admin_key or not secrets.compare_digest(secret_key, admin_key):
        raise ValueError("Invalid admin secret key!")

    conn = get_db_connection()
    cursor = conn.cursor()
    # Check if staff_id already exists among admins only
    cursor.execute("SELECT * FROM users WHERE staff_id=%s AND role='admin'", (staff_id,))
    if cursor.fetchone():
        conn.close()
        raise ValueError("Staff ID already exists!")
    hashed_pw = generate_password_hash(password)
    cursor.execute(
        "INSERT INTO users (name, staff_id, password, role) VALUES (%s, %s, %s, %s)",
        (name, staff_id, hashed_pw, "admin")
    )
    conn.commit()
    conn.close()

def login_user(staff_id, password, role="student"):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users WHERE staff_id=%s AND role=%s", (staff_id, role))
    user = cursor.fetchone()

    if user and check_password_hash(user["password"], password):
        token = str(uuid.uuid4())
        cursor.execute("UPDATE users SET session_token=%s WHERE id=%s", (token, user["id"]))
        conn.commit()
        conn.close()
        user["session_token"] = token
        return user
    conn.close()
    return None

def get_user_by_token(token):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users WHERE session_token=%s", (token,))
    user = cursor.fetchone()
    conn.close()
    return user

def clear_user_token(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET session_token=NULL WHERE id=%s", (user_id,))
    conn.commit()
    conn.close()


def verify_security_answer(stored_answer, given_answer):
    """Check new hashed answers, while accepting existing demo records."""
    answer = given_answer.strip().lower()
    if stored_answer.startswith(("scrypt:", "pbkdf2:")):
        return check_password_hash(stored_answer, answer)
    return secrets.compare_digest(stored_answer, answer)
