from flask import Flask, request, jsonify, g, send_from_directory, abort
from flask_cors import CORS
from database import get_db_connection
from werkzeug.security import check_password_hash, generate_password_hash
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from dotenv import load_dotenv
import os
import subprocess
import sys
import secrets
import json
import urllib.request
from functools import wraps
from datetime import date, datetime, timedelta
from pathlib import Path


# =====================================================
# LOAD ENVIRONMENT VARIABLES
# =====================================================

APP_DIR = Path(__file__).resolve().parent
ENV_FILE = APP_DIR / ".env"
load_dotenv(ENV_FILE, override=False)
# The supplied project bundle uses this name for its private local settings.
load_dotenv(APP_DIR / "_env", override=False)


def _load_or_create_secret_key():
    configured_key = os.getenv("FLASK_SECRET_KEY")
    if configured_key:
        return configured_key

    secret_file = APP_DIR / ".interview_mate_secret"
    try:
        if secret_file.exists():
            saved_key = secret_file.read_text(encoding="utf-8").strip()
            if saved_key:
                return saved_key
        generated_key = secrets.token_hex(32)
        secret_file.write_text(generated_key, encoding="utf-8")
        return generated_key
    except OSError:
        return secrets.token_hex(32)

RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET")
RAZORPAY_ENABLED = os.getenv("ENABLE_RAZORPAY_PAYMENTS", "").strip().lower() == "true"


# =====================================================
# RAZORPAY CLIENT
# =====================================================

try:
    import razorpay
except ImportError:
    razorpay = None

razorpay_client = (
    razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
    if RAZORPAY_ENABLED and razorpay and RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET
    else None
)


# =====================================================
# FLASK APP
# =====================================================

app = Flask(__name__)
app.config["SECRET_KEY"] = _load_or_create_secret_key()
APP_DIR = str(APP_DIR)
token_serializer = URLSafeTimedSerializer(
    app.config["SECRET_KEY"],
    salt="interview-mate-session"
)

CORS(app)

def authenticated_user_id():
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        return None
    token = authorization[7:].strip()
    try:
        payload = token_serializer.loads(token, max_age=86400)
        return int(payload["user_id"])
    except (BadSignature, SignatureExpired, KeyError, TypeError, ValueError):
        return None


def require_auth(route):
    @wraps(route)
    def wrapped(*args, **kwargs):
        user_id = authenticated_user_id()
        if not user_id:
            return jsonify({"message": "Please sign in again to continue."}), 401
        g.user_id = user_id
        return route(*args, **kwargs)
    return wrapped


def require_account_match(route):
    @wraps(route)
    def wrapped(*args, **kwargs):
        signed_in_user_id = authenticated_user_id()
        requested_user_id = kwargs.get("user_id")
        if requested_user_id is None:
            data = request.get_json(silent=True) or {}
            requested_user_id = data.get("user_id")
        if requested_user_id is None:
            requested_user_id = request.args.get("user_id")
        try:
            requested_user_id = int(requested_user_id)
        except (TypeError, ValueError):
            requested_user_id = None
        if not signed_in_user_id:
            return jsonify({"message": "Please sign in again to continue."}), 401
        if requested_user_id != signed_in_user_id:
            return jsonify({"message": "This action is not available for that account."}), 403
        g.user_id = signed_in_user_id
        return route(*args, **kwargs)
    return wrapped


# =====================================================
# RAZORPAY PLANS
# =====================================================

RAZORPAY_PLANS = {
    "basic": {
        "name": "Basic",
        "amount": 20,
        "plan_id": "plan_TiE4HCfL9Ek3TX"
    },
    "standard": {
        "name": "Standard",
        "amount": 30,
        "plan_id": "plan_TiE56IpnSMK9fs"
    },
    "premium": {
        "name": "Premium",
        "amount": 40,
        "plan_id": "plan_TiE5kHyjowPrTy"
    }
}
# =====================================================
# HOME
# =====================================================

@app.route("/")
def home():
    return send_from_directory(APP_DIR, "index.html")


@app.route("/<path:filename>")
def frontend_file(filename):
    # Serve only the public files required by the frontend. In particular,
    # never expose .env, app.py, database.py, or other backend files here.
    if filename not in {"index.html", "signup.html", "dashboard-preview.html", "style.css", "design.css", "theme.css", "script.js"}:
        abort(404)
    return send_from_directory(APP_DIR, filename)


# =====================================================
# TEST DATABASE
# =====================================================

@app.route("/test-db")
def test_db():

    try:
        db = get_db_connection()
        cursor = db.cursor()

        cursor.execute("SELECT 1")

        result = cursor.fetchone()[0]

        cursor.close()
        db.close()

        return jsonify({
            "success": True,
            "message": "Database connected successfully",
            "result": result
        })

    except Exception as e:

        return jsonify({
            "success": False,
            "message": "Database connection failed. Check the local database settings."
        }), 500


# =====================================================
# REGISTER
# =====================================================

@app.route("/register", methods=["POST"])
def register():

    data = request.get_json()

    if not data:
        return jsonify({
            "message": "No data received"
        }), 400

    name = str(data.get("name") or "").strip()
    email = str(data.get("email") or "").strip().lower()
    password = data.get("password")
    phone = str(data.get("phone") or "").strip() or None
    college = str(data.get("college") or "").strip() or None
    course = str(data.get("course") or "").strip() or None
    year = str(data.get("year") or "").strip() or None

    if not name or not email or not isinstance(password, str) or not password:

        return jsonify({
            "message": "All fields are required"
        }), 400

    try:

        db = get_db_connection()
        cursor = db.cursor()

        cursor.execute(
            "SELECT id FROM users WHERE email = %s",
            (email,)
        )

        existing_user = cursor.fetchone()

        if existing_user:

            cursor.close()
            db.close()

            return jsonify({
                "message": "Email already registered"
            }), 409

        cursor.execute(
            """
            INSERT INTO users
            (name, email, password, phone, college, course, year)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                name,
                email,
                generate_password_hash(password),
                phone,
                college,
                course,
                year
            )
        )

        db.commit()

        cursor.close()
        db.close()

        return jsonify({
            "message": "Registration successful"
        }), 201

    except Exception as e:

        return jsonify({
            "message": "Registration could not be completed. Please try again."
        }), 500


@app.route("/ai/hr-interview", methods=["POST"])
@require_auth
def hr_interview_response():
    data = request.get_json(silent=True) or {}
    history = data.get("history")
    try:
        question_number = int(data.get("question_number", 0))
        total_questions = int(data.get("total_questions", 5))
    except (TypeError, ValueError):
        return jsonify({"message": "The interview turn is invalid."}), 400
    if not isinstance(history, list) or not history or len(history) > 12:
        return jsonify({"message": "The interview conversation is invalid."}), 400
    if not 1 <= question_number <= 5 or total_questions != 5:
        return jsonify({"message": "The interview turn is invalid."}), 400
    safe_history = []
    for message in history:
        if not isinstance(message, dict) or message.get("role") not in {"assistant", "user"}:
            return jsonify({"message": "The interview conversation is invalid."}), 400
        content = str(message.get("content") or "").strip()
        if not content or len(content) > 2000:
            return jsonify({"message": "Each interview message must be between 1 and 2,000 characters."}), 400
        safe_history.append({"role": message["role"], "content": content})
    if safe_history[-1]["role"] != "user":
        return jsonify({"message": "Submit an answer before requesting feedback."}), 400

    final_turn = question_number == total_questions
    fallback_question = str(data.get("fallback_question") or "").strip()
    if not final_turn and (not fallback_question or len(fallback_question) > 300):
        return jsonify({"message": "The next interview question is invalid."}), 400

    instructions = (
        "You are a supportive, realistic HR interview coach. Review the candidate's most recent answer in context. "
        "Give concise, specific feedback that mentions one strength and one practical improvement when appropriate. "
        "Be respectful, avoid inventing facts, and keep feedback to 2-4 sentences. "
        + ("This is the last question. End with a short overall interview summary and do not ask another question. "
           "Put the summary under the exact heading FINAL_SUMMARY:." if final_turn else
           "Then ask exactly one natural next HR interview question that follows from the candidate's answers. "
           "Put the next question on the final line after the exact heading NEXT_QUESTION:.")
    )
    try:
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("Gemini_API_Key")
        if not api_key:
            raise RuntimeError("No Gemini API key is configured.")

        conversation = "\n".join(
            f"{'Interviewer' if message['role'] == 'assistant' else 'Candidate'}: {message['content']}"
            for message in safe_history
        )
        request_body = json.dumps({
            "model": os.getenv("GEMINI_MODEL", "gemini-flash-latest"),
            "system_instruction": instructions,
            "input": conversation,
            "store": False,
        }).encode("utf-8")
        api_request = urllib.request.Request(
            "https://generativelanguage.googleapis.com/v1beta/interactions",
            data=request_body,
            headers={
                "x-goog-api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(api_request, timeout=18) as response:
            result = json.loads(response.read().decode("utf-8"))
        output_text = "\n".join(
            item.get("text", "")
            for step in result.get("steps", [])
            if step.get("type") == "model_output"
            for item in step.get("content", [])
            if item.get("type") == "text" and item.get("text")
        ).strip()
        if not output_text:
            raise ValueError("The AI service returned an empty response.")
        return jsonify({"reply": output_text, "mode": "gemini"}), 200
    except Exception as error:
        app.logger.warning(
            "Gemini interview response unavailable; using the offline coach (%s).",
            type(error).__name__,
        )

    answer = safe_history[-1]["content"]
    word_count = len(answer.split())
    answer_lower = answer.lower()
    result_words = ("result", "learned", "improved", "completed", "saved", "increased", "reduced", "achieved")
    action_words = ("i ", "we ", "built", "organized", "created", "resolved", "tested", "planned", "helped")
    if word_count >= 25:
        strength = "You gave a detailed answer that gives the interviewer useful context."
    else:
        strength = "You answered the question directly and kept your main point clear."
    if any(word in answer_lower for word in action_words):
        improvement = "Make your personal contribution easy to spot, then finish with the result."
    else:
        improvement = "Add the specific action you personally took and what changed because of it."
    if any(word in answer_lower for word in result_words):
        improvement = "Make the outcome more concrete with a number, example, or lesson learned."

    reply = f"FEEDBACK: {strength} {improvement}"
    if final_turn:
        reply += "\n\nFINAL_SUMMARY: Keep using a clear situation, action, and result. Your next improvement is to connect each example to the role and make the outcome specific."
    else:
        reply += f"\nNEXT_QUESTION: {fallback_question}"
    return jsonify({"reply": reply, "mode": "offline"}), 200


# =====================================================
# LOGIN
# =====================================================

@app.route("/login", methods=["POST"])
def login():

    data = request.get_json()

    if not data:

        return jsonify({
            "message": "No data received"
        }), 400

    email = str(data.get("email") or "").strip().lower()
    password = data.get("password")

    if not email or not password:

        return jsonify({
            "message": "Email and password are required"
        }), 400

    try:

        db = get_db_connection()

        cursor = db.cursor(
            dictionary=True
        )

        cursor.execute(
            """
            SELECT id, name, email, password
            FROM users
            WHERE email = %s
            """,
            (email,)
        )

        user = cursor.fetchone()

        if user:
            stored_password = user.pop("password")
            valid_password = False
            try:
                valid_password = check_password_hash(stored_password, password)
            except (TypeError, ValueError):
                valid_password = False

            # Upgrade existing plain-text passwords on successful login.
            if not valid_password and stored_password == password:
                valid_password = True
                cursor.execute(
                    "UPDATE users SET password = %s WHERE id = %s",
                    (generate_password_hash(password), user["id"])
                )
                db.commit()

            if not valid_password:
                user = None

        cursor.close()
        db.close()

        if not user:

            return jsonify({
                "message": "Invalid email or password"
            }), 401

        return jsonify({
            "message": "Login successful",
            "user": user,
            "token": token_serializer.dumps({"user_id": user["id"]})
        }), 200

    except Exception as e:

        return jsonify({"message": "Could not sign in. Please try again."}), 500
# =====================================================
# USER PROFILE
# =====================================================

@app.route("/profile", methods=["GET"])
@require_auth
def get_profile():
    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT id, name, email, phone, college, course, year
            FROM users
            WHERE id = %s
            """,
            (g.user_id,)
        )

        user = cursor.fetchone()

        if not user:
            return jsonify({"message": "User not found."}), 404

        return jsonify({"user": user}), 200

    except Exception:
        app.logger.exception("Failed to retrieve user profile")
        return jsonify({"message": "Could not load your profile."}), 500

    finally:
        if cursor:
            cursor.close()
        if db:
            db.close()


@app.route("/profile", methods=["PUT"])
@require_auth
def update_profile():
    db = None
    cursor = None

    try:
        data = request.get_json(silent=True) or {}

        name = str(data.get("name", "")).strip()
        email = str(data.get("email", "")).strip().lower()
        phone = str(data.get("phone") or "").strip() or None
        college = str(data.get("college") or "").strip() or None
        course = str(data.get("course") or "").strip() or None
        year = str(data.get("year") or "").strip() or None

        if not name or not email:
            return jsonify({
                "message": "Name and email are required."
            }), 400

        if len(name) > 100 or len(email) > 150:
            return jsonify({
                "message": "Name or email is too long."
            }), 400

        if phone and len(phone) > 20:
            return jsonify({
                "message": "Phone number is too long."
            }), 400

        if college and len(college) > 150:
            return jsonify({
                "message": "College name is too long."
            }), 400

        if course and len(course) > 100:
            return jsonify({
                "message": "Course name is too long."
            }), 400

        if year and len(year) > 50:
            return jsonify({
                "message": "Graduation year is too long."
            }), 400

        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        # Prevent another account from using the same email.
        cursor.execute(
            """
            SELECT id FROM users
            WHERE email = %s AND id != %s
            """,
            (email, g.user_id)
        )

        if cursor.fetchone():
            return jsonify({
                "message": "This email is already used by another account."
            }), 409

        cursor.execute(
            """
            UPDATE users
            SET name = %s,
                email = %s,
                phone = %s,
                college = %s,
                course = %s,
                year = %s
            WHERE id = %s
            """,
            (
                name,
                email,
                phone,
                college,
                course,
                year,
                g.user_id
            )
        )

        db.commit()

        cursor.execute(
            """
            SELECT id, name, email, phone, college, course, year
            FROM users
            WHERE id = %s
            """,
            (g.user_id,)
        )

        user = cursor.fetchone()

        if not user:
            return jsonify({"message": "User not found."}), 404

        return jsonify({
            "message": "Profile updated successfully.",
            "user": user
        }), 200

    except Exception:
        if db:
            db.rollback()

        app.logger.exception("Failed to update user profile")

        return jsonify({
            "message": "Could not save your profile."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if db:
            db.close()


@app.route("/change-password", methods=["POST"])
@require_auth
def change_password():
    db = None
    cursor = None
    data = request.get_json(silent=True) or {}
    current_password = data.get("current_password")
    new_password = data.get("new_password")

    if not isinstance(current_password, str) or not current_password:
        return jsonify({"message": "Enter your current password."}), 400
    if not isinstance(new_password, str) or len(new_password) < 8:
        return jsonify({"message": "Choose a new password with at least 8 characters."}), 400
    if len(new_password) > 256:
        return jsonify({"message": "The new password is too long."}), 400
    if current_password == new_password:
        return jsonify({"message": "Choose a new password that is different from your current password."}), 400

    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT password FROM users WHERE id = %s", (g.user_id,))
        user = cursor.fetchone()
        if not user:
            return jsonify({"message": "Account not found."}), 404

        stored_password = user.get("password") or ""
        try:
            valid_password = check_password_hash(stored_password, current_password)
        except (TypeError, ValueError):
            valid_password = False
        if not valid_password and stored_password == current_password:
            valid_password = True
        if not valid_password:
            return jsonify({"message": "Your current password is incorrect."}), 401

        cursor.execute(
            "UPDATE users SET password = %s WHERE id = %s",
            (generate_password_hash(new_password), g.user_id)
        )
        db.commit()
        return jsonify({"message": "Your password has been updated."}), 200
    except Exception:
        if db:
            db.rollback()
        app.logger.exception("Failed to update user password")
        return jsonify({"message": "Could not update your password."}), 500
    finally:
        if cursor:
            cursor.close()
        if db:
            db.close()

# =====================================================
# SAVE TEST RESULT
# =====================================================

@app.route("/save-result", methods=["POST"])
@require_account_match
def save_result():

    data = request.get_json()

    if not data:

        return jsonify({
            "message": "No data received"
        }), 400

    user_id = data.get("user_id")
    test_type = data.get("test_type")
    score = data.get("score")
    total = data.get("total")
    percentage = data.get("percentage")

    if (
        not user_id
        or not test_type
        or score is None
        or not total
    ):

        return jsonify({
            "message": "Missing result information"
        }), 400

    try:
        user_id = int(user_id)
        score = int(score)
        total = int(total)
        if user_id < 1 or total < 1 or score < 0 or score > total:
            return jsonify({"message": "Invalid assessment result."}), 400
        if test_type not in ("Aptitude", "Coding", "HR", "Mock Test"):
            return jsonify({"message": "Unknown assessment type."}), 400
        percentage = round((score / total) * 100)

        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("SELECT id FROM users WHERE id = %s", (user_id,))
        if not cursor.fetchone():
            cursor.close()
            db.close()
            return jsonify({"message": "User account not found."}), 404

        cursor.execute(
            """
            INSERT INTO test_results
            (
                user_id, test_type, score, total, percentage
            )
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                user_id,
                test_type,
                score,
                total,
                percentage
            )
        )

        db.commit()

        cursor.close()
        db.close()

        return jsonify({
            "message": "Test result saved successfully"
        }), 201

    except Exception as e:

        return jsonify({
            "message": "Could not save the assessment result."
        }), 500


# =====================================================
# DASHBOARD STATISTICS
# =====================================================

@app.route(
    "/dashboard-stats/<int:user_id>",
    methods=["GET"]
)
@require_account_match
def dashboard_stats(user_id):

    try:

        db = get_db_connection()

        cursor = db.cursor(
            dictionary=True
        )

        cursor.execute(
    """
    SELECT
        id,
        test_type,
        score,
        total,
        percentage,
        taken_at AS created_at
    FROM test_results
    WHERE user_id = %s
    ORDER BY taken_at ASC, id ASC
    """,
    (user_id,)
)
        results = cursor.fetchall()

        cursor.execute(
            """
            SELECT goal_count
            FROM daily_goals
            WHERE user_id = %s AND goal_date = CURDATE()
            """,
            (user_id,)
        )
        goal_row = cursor.fetchone()

        cursor.close()
        db.close()

        tests_completed = len(results)

        latest = {}
        daily_counts = {}
        history = []

        for result in results:

            test_type = result["test_type"]
            percentage = int(round(float(result["percentage"] or 0)))
            latest[test_type] = percentage
            created = result["created_at"]
            if isinstance(created, str):
                try:
                    created = datetime.fromisoformat(created)
                except ValueError:
                    try:
                        created = date.fromisoformat(created)
                    except ValueError:
                        pass
            result_day = created.date() if isinstance(created, datetime) else created
            if isinstance(result_day, date):
                daily_counts[result_day.isoformat()] = daily_counts.get(result_day.isoformat(), 0) + 1
                created_label = created.isoformat()
            else:
                created_label = str(created)
            history.append({
                "id": result["id"],
                "testType": test_type,
                "score": int(result["score"]),
                "total": int(result["total"]),
                "percentage": percentage,
                "createdAt": created_label
            })

        aptitude_progress = latest.get("Aptitude", 0)
        coding_progress = latest.get("Coding", 0)
        hr_progress = latest.get("HR", 0)

        if results:

            average_score = round(
                sum(
                    float(r["percentage"] or 0)
                    for r in results
                ) / len(results)
            )

        else:

            average_score = 0

        overall_progress = round(
            (
                aptitude_progress
                + coding_progress
                + hr_progress
            ) / 3
        )

        today = date.today()
        weekly_activity = []
        for offset in range(6, -1, -1):
            day = today - timedelta(days=offset)
            weekly_activity.append({
                "day": day.strftime("%a"),
                "date": day.isoformat(),
                "count": daily_counts.get(day.isoformat(), 0)
            })

        active_days = {day for day, count in daily_counts.items() if count}
        streak = 0
        check_day = today if today.isoformat() in active_days else today - timedelta(days=1)
        while check_day.isoformat() in active_days:
            streak += 1
            check_day -= timedelta(days=1)

        today_count = daily_counts.get(today.isoformat(), 0)
        daily_goal = int(goal_row["goal_count"]) if goal_row else 1
        weekly_activity_percent = round(
            100 * sum(1 for day in weekly_activity if day["count"]) / 7
        )

        return jsonify({

            "testsCompleted":
                tests_completed,

            "averageScore":
                average_score,

            "aptitudeProgress":
                aptitude_progress,

            "codingProgress":
                coding_progress,

            "hrProgress":
                hr_progress,

            "overallProgress":
                overall_progress,

            "currentStreak": streak,
            "weeklyActivity": weekly_activity,
            "weeklyActivityPercent": weekly_activity_percent,
            "dailyGoal": daily_goal,
            "dailyGoalCompleted": today_count,
            "history": history[-30:]

        }), 200

    except Exception:
        app.logger.exception("Dashboard stats error")

    return jsonify({
        "message": "Could not load this user's progress."
    }), 500

@app.route("/leaderboard", methods=["GET"])
@require_auth
def leaderboard():
    """Return a simple score ranking without exposing account details."""
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT
                u.id,
                u.name,
                COUNT(r.id) AS tests_completed,
                COALESCE(ROUND(AVG(r.percentage)), 0) AS average_score
            FROM users AS u
            LEFT JOIN test_results AS r ON r.user_id = u.id
            GROUP BY u.id, u.name
            ORDER BY average_score DESC, tests_completed DESC, u.name ASC
            """
        )
        rows = cursor.fetchall()
        cursor.close()
        db.close()
        ranked = [
            {
                "rank": index,
                "id": row["id"],
                "name": row["name"],
                "testsCompleted": int(row["tests_completed"]),
                "averageScore": int(round(float(row["average_score"] or 0)))
            }
            for index, row in enumerate(rows, start=1)
        ]
        current_rank = next(
            (entry["rank"] for entry in ranked if entry["id"] == g.user_id),
            None
        )
        return jsonify({
            "leaderboard": ranked[:20],
            "currentUserRank": current_rank
        }), 200
    except Exception as e:
        return jsonify({"message": "Could not load the leaderboard."}), 500


@app.route("/study-data/<int:user_id>", methods=["GET"])
@require_account_match
def study_data(user_id):
    """Load only the signed-in user's daily goal and planner tasks."""
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            "SELECT goal_count FROM daily_goals WHERE user_id = %s AND goal_date = CURDATE()",
            (user_id,)
        )
        goal_row = cursor.fetchone()
        cursor.execute(
            """
            SELECT id, title, scheduled_for, is_done
            FROM study_tasks
            WHERE user_id = %s
            ORDER BY scheduled_for ASC, id ASC
            LIMIT 100
            """,
            (user_id,)
        )
        tasks = cursor.fetchall()
        cursor.close()
        db.close()
        return jsonify({
            "dailyGoal": int(goal_row["goal_count"]) if goal_row else 1,
            "tasks": [
                {
                    "id": task["id"],
                    "title": task["title"],
                    "scheduledFor": (
                        task["scheduled_for"].isoformat()
                        if hasattr(task["scheduled_for"], "isoformat")
                        else str(task["scheduled_for"])
                    ),
                    "done": bool(task["is_done"])
                }
                for task in tasks
            ]
        }), 200
    except Exception as e:
        return jsonify({"message": "Could not load your study plan."}), 500


@app.route("/daily-goal", methods=["POST"])
@require_account_match
def save_daily_goal():
    data = request.get_json() or {}
    try:
        user_id = int(data.get("user_id", 0))
        goal_count = int(data.get("goal_count", 0))
    except (TypeError, ValueError):
        return jsonify({"message": "Enter a whole number for your daily goal."}), 400
    if user_id < 1 or not 1 <= goal_count <= 20:
        return jsonify({"message": "Choose a daily goal from 1 to 20."}), 400
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute(
            """
            INSERT INTO daily_goals (user_id, goal_date, goal_count)
            VALUES (%s, CURDATE(), %s)
            ON DUPLICATE KEY UPDATE goal_count = VALUES(goal_count)
            """,
            (user_id, goal_count)
        )
        db.commit()
        cursor.close()
        db.close()
        return jsonify({"message": "Daily goal saved.", "dailyGoal": goal_count}), 200
    except Exception as e:
        return jsonify({"message": "Could not save your daily goal."}), 500


@app.route("/planner", methods=["POST"])
@require_account_match
def add_study_task():
    data = request.get_json() or {}
    title = str(data.get("title", "")).strip()
    try:
        user_id = int(data.get("user_id", 0))
        scheduled_for = date.fromisoformat(str(data.get("scheduled_for", "")))
    except (TypeError, ValueError):
        return jsonify({"message": "Choose a valid date for the study task."}), 400
    if user_id < 1 or not title or len(title) > 120:
        return jsonify({"message": "Add a task title (up to 120 characters)."}), 400
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO study_tasks (user_id, title, scheduled_for) VALUES (%s, %s, %s)",
            (user_id, title, scheduled_for)
        )
        db.commit()
        task_id = cursor.lastrowid
        cursor.close()
        db.close()
        return jsonify({"id": task_id, "message": "Study task added."}), 201
    except Exception as e:
        return jsonify({"message": "Could not add the study task."}), 500


@app.route("/planner/<int:task_id>/toggle", methods=["POST"])
@require_account_match
def toggle_study_task(task_id):
    data = request.get_json() or {}
    try:
        user_id = int(data.get("user_id", 0))
    except (TypeError, ValueError):
        user_id = 0
    if user_id < 1:
        return jsonify({"message": "Sign in to update your study plan."}), 401
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute(
            "UPDATE study_tasks SET is_done = NOT is_done WHERE id = %s AND user_id = %s",
            (task_id, user_id)
        )
        db.commit()
        updated = cursor.rowcount
        cursor.close()
        db.close()
        if not updated:
            return jsonify({"message": "Study task not found."}), 404
        return jsonify({"message": "Study task updated."}), 200
    except Exception as e:
        return jsonify({"message": "Could not update the study task."}), 500


@app.route("/planner/<int:task_id>", methods=["DELETE"])
@require_account_match
def delete_study_task(task_id):
    try:
        user_id = int(request.args.get("user_id", 0))
    except (TypeError, ValueError):
        user_id = 0
    if user_id < 1:
        return jsonify({"message": "Sign in to update your study plan."}), 401
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute(
            "DELETE FROM study_tasks WHERE id = %s AND user_id = %s",
            (task_id, user_id)
        )
        db.commit()
        deleted = cursor.rowcount
        cursor.close()
        db.close()
        if not deleted:
            return jsonify({"message": "Study task not found."}), 404
        return jsonify({"message": "Study task removed."}), 200
    except Exception as e:
        return jsonify({"message": "Could not remove the study task."}), 500


# =====================================================
# RUN CODING CODE
# =====================================================

@app.route("/run-code", methods=["POST"])
@require_auth
def run_code():

    data = request.get_json()

    if not data:

        return jsonify({
            "success": False,
            "output": "No data received."
        }), 400

    code = data.get("code")

    if not code:

        return jsonify({

            "success": False,
            "output": "No code provided."

        }), 400

    try:

        result = subprocess.run(

            [
                sys.executable,
                "-c",
                code
            ],

            capture_output=True,

            text=True,

            timeout=15
        )

        if result.returncode == 0:

            return jsonify({

                "success": True,

                "output":
                    result.stdout

            })

        else:

            return jsonify({

                "success": False,

                "output":
                    result.stderr

            })

    except subprocess.TimeoutExpired:

        return jsonify({

            "success": False,

            "output":
                "⏱️ Code took too long to run."

        })

    except Exception as e:

        return jsonify({

            "success": False,

            "output":
                str(e)

        }), 500


# =====================================================
# RAZORPAY CREATE SUBSCRIPTION
# =====================================================

@app.route(
    "/create-subscription",
    methods=["POST"]
)
# =====================================================
# RAZORPAY CREATE SUBSCRIPTION
# =====================================================

@app.route("/create-subscription", methods=["POST"])
@require_account_match
def create_subscription():

    try:
        data = request.get_json() or {}

        plan = data.get("plan")
        user_id = data.get("user_id")

        # Check plan
        if plan not in RAZORPAY_PLANS:
            return jsonify({
                "success": False,
                "message": "Invalid subscription plan."
            }), 400

        # Check user
        if not user_id:
            return jsonify({
                "success": False,
                "message": "User ID is required."
            }), 400

        if not razorpay_client:
            return jsonify({
                "success": False,
                "message": "Online payments are not enabled for this demo."
            }), 503

        # Check user exists
        db = get_db_connection()
        cursor = db.cursor()

        cursor.execute(
            "SELECT id FROM users WHERE id = %s",
            (user_id,)
        )

        user = cursor.fetchone()

        cursor.close()
        db.close()

        if not user:
            return jsonify({
                "success": False,
                "message": "User not found."
            }), 404

        # Selected plan
        selected_plan = RAZORPAY_PLANS[plan]

        # Create Razorpay subscription
        subscription_data = {
            "plan_id": selected_plan["plan_id"],
            "total_count": 12,
            "quantity": 1,
            "customer_notify": 1
        }

        subscription = razorpay_client.subscription.create(
            subscription_data
        )

        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute(
            """
            INSERT INTO subscriptions
            (user_id, plan, amount, razorpay_subscription_id, razorpay_payment_id, status)
            VALUES (%s, %s, %s, %s, %s, 'pending')
            """,
            (
                user_id,
                selected_plan["name"],
                selected_plan["amount"],
                subscription["id"],
                ""
            )
        )
        db.commit()
        cursor.close()
        db.close()

        return jsonify({
            "success": True,
            "key_id": RAZORPAY_KEY_ID,
            "subscription_id": subscription["id"],
            "plan": selected_plan["name"],
            "amount": selected_plan["amount"]
        }), 200

    except Exception as e:

        return jsonify({
            "success": False,
            "message": "Could not create Razorpay subscription. Check server configuration and try again."
        }), 500
# =====================================================
# RAZORPAY VERIFY SUBSCRIPTION
# =====================================================

@app.route(
    "/verify-subscription",
    methods=["POST"]
)
@require_account_match
def verify_subscription():

    data = request.get_json()

    if not data:

        return jsonify({

            "success": False,

            "message":
                "No data received."

        }), 400

    payment_id = data.get(
        "razorpay_payment_id"
    )

    subscription_id = data.get(
        "razorpay_subscription_id"
    )

    signature = data.get(
        "razorpay_signature"
    )

    user_id = data.get(
        "user_id"
    )

    plan = data.get(
        "plan"
    )

    if not payment_id:

        return jsonify({

            "success": False,

            "message":
                "Payment ID is missing."

        }), 400

    if not subscription_id:

        return jsonify({

            "success": False,

            "message":
                "Subscription ID is missing."

        }), 400

    if not signature:

        return jsonify({

            "success": False,

            "message":
                "Payment signature is missing."

        }), 400

    if not user_id:

        return jsonify({

            "success": False,

            "message":
                "User ID is missing."

        }), 400

    if plan not in RAZORPAY_PLANS:

        return jsonify({

            "success": False,

            "message":
                "Invalid plan."

        }), 400

    try:
        selected_plan = RAZORPAY_PLANS[plan]
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute(
            """
            SELECT id
            FROM subscriptions
            WHERE user_id = %s
              AND plan = %s
              AND razorpay_subscription_id = %s
              AND status = 'pending'
            LIMIT 1
            """,
            (
                user_id,
                selected_plan["name"],
                subscription_id
            )
        )
        pending_subscription = cursor.fetchone()
        cursor.close()
        db.close()
        if not pending_subscription:
            return jsonify({
                "success": False,
                "message": "This payment session does not belong to your account."
            }), 403

        razorpay_client.utility.verify_subscription_payment_signature({
            "razorpay_payment_id": payment_id,
            "razorpay_subscription_id": subscription_id,
            "razorpay_signature": signature
        })

        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute(
            """
            UPDATE subscriptions
            SET razorpay_payment_id = %s, status = 'active'
            WHERE id = %s AND user_id = %s AND status = 'pending'
            """,
            (payment_id, pending_subscription[0], user_id)
        )
        db.commit()
        updated = cursor.rowcount
        cursor.close()
        db.close()
        if not updated:
            return jsonify({
                "success": False,
                "message": "This payment session has already been processed."
            }), 409

        return jsonify({

            "success": True,

            "message":
                "Subscription activated successfully.",

            "subscription_id":
                subscription_id,

            "plan":
                selected_plan["name"]

        }), 200

    except Exception as e:
        return jsonify({

            "success": False,

            "message":
                "Payment verification failed."

        }), 400

# ==========================================================
# CHECK USER SUBSCRIPTION
# ==========================================================

@app.route("/subscription-status/<int:user_id>", methods=["GET"])
@require_account_match
def subscription_status(user_id):

    try:

        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                id,
                plan,
                amount,
                razorpay_subscription_id,
                status,
                created_at
            FROM subscriptions
            WHERE user_id = %s
              AND status = 'active'
            ORDER BY id DESC
            LIMIT 1
        """, (user_id,))

        subscription = cursor.fetchone()

        cursor.close()
        db.close()

        # User has an active subscription
        if subscription:

            return jsonify({
                "success": True,
                "subscribed": True,
                "plan": subscription["plan"],
                "amount": float(subscription["amount"]),
                "subscription_id":
                    subscription["razorpay_subscription_id"]
            }), 200

        # User has no active subscription
        return jsonify({
            "success": True,
            "subscribed": False
        }), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "subscribed": False,
            "message": "Could not check subscription status."
        }), 500
# =====================================================
# START SERVER
# =====================================================

if __name__ == "__main__":

    app.run(
        debug=False
    )
