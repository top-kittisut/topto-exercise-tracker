from flask import Flask, render_template, request, redirect, url_for, session
import datetime
import json
import os
import firebase_admin
from firebase_admin import credentials, db

app = Flask(__name__)
app.secret_key = "some-random-secret"

# Load Firebase credentials from an environment variable
firebase_creds_json = os.environ.get("FIREBASE_CREDENTIALS")
if not firebase_creds_json:
    raise Exception("FIREBASE_CREDENTIALS environment variable not set")

firebase_creds = json.loads(firebase_creds_json)
cred = credentials.Certificate(firebase_creds)

# Initialize Firebase Admin
firebase_admin.initialize_app(cred, {
    'databaseURL': 'https://topto-exercise-tracker-default-rtdb.firebaseio.com/'  # Replace with your actual database URL
})

def load_users():
    ref = db.reference("/users")
    data = ref.get()
    return data if data is not None else {}

def save_users(users_data):
    ref = db.reference("/users")
    ref.set(users_data)

# Load user data from Firebase at startup
users = load_users()
# We'll keep a global incremental ID for each exercise.
next_exercise_id = 1

def get_week_start_end(date_obj):
    """
    Given a date, returns the start (Sunday) and end (Saturday) of that week.
    """
    weekday = date_obj.weekday()  # Monday=0 ... Sunday=6
    delta_to_sunday = (weekday + 1) % 7
    start_of_week = date_obj - datetime.timedelta(days=delta_to_sunday)
    end_of_week = start_of_week + datetime.timedelta(days=6)
    return start_of_week, end_of_week

def calculate_points_for_user(username):
    """
    Summarize from March 2, 2025 to June 30, 2025:
      - 1 point per day if daily "effective" minutes >= 30
      - "Hike" with >=10000 steps => treat as 30 min
      - max 5 points per Sunday-Saturday week
    """
    if username not in users:
        return 0

    start_comp = datetime.date(2025, 3, 2)
    end_comp = datetime.date(2025, 6, 30)

    # Build daily total effective minutes
    date_minutes_map = {}
    for ex in users[username]["exercises"]:
        # Parse the date
        try:
            ex_date = datetime.datetime.strptime(ex["date"], "%Y-%m-%d").date()
        except ValueError:
            continue  # skip invalid date
        # Skip outside competition range
        if not (start_comp <= ex_date <= end_comp):
            continue

        # Determine effective minutes
        if ex["activity_type"] == "hike":
            # If hike & steps >= 10000 => 30 minutes
            if ex.get("steps", 0) >= 10000:
                effective_minutes = 30
            else:
                effective_minutes = 0
        else:
            effective_minutes = ex["time"]

        date_minutes_map.setdefault(ex_date, 0)
        date_minutes_map[ex_date] += effective_minutes

    all_dates = sorted(date_minutes_map.keys())

    points_total = 0
    current_week_start = None
    current_week_end = None
    day_count_in_week = 0

    for d in all_dates:
        # If we're outside the current week's range, finalize the old week
        if current_week_start is None or not (current_week_start <= d <= current_week_end):
            points_total += min(day_count_in_week, 5)
            ws, we = get_week_start_end(d)
            current_week_start, current_week_end = ws, we
            day_count_in_week = 0

        # If day total >= 30 => user gets 1 daily point (subject to weekly cap)
        if date_minutes_map[d] >= 30:
            day_count_in_week += 1

    # Add the last partial week's points
    points_total += min(day_count_in_week, 5)
    return points_total

@app.route("/")
def index():
    # Pass the list of usernames to the front page
    user_list = list(users.keys())
    return render_template("index.html", user_list=user_list)

# @app.route("/signup", methods=["GET", "POST"])
# def signup():
#     if request.method == "POST":
#         username = request.form["username"].strip()
#         password = request.form["password"].strip()
#         if username in users:
#             return "<p>Username already exists. Go back and try again.</p>"
#         users[username] = {
#             "password": password,
#             "exercises": []
#         }
#         return redirect(url_for("login"))
#     return render_template("signup.html")

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()
        if username in users:
            return "<p>Username already exists. Go back and try again.</p>"
        users[username] = {
            "password": password,
            "exercises": []
        }
        save_users(users)  # Save after new signup
        return redirect(url_for("login"))
    return render_template("signup.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()
        if username in users and users[username]["password"] == password:
            session["username"] = username
            return redirect(url_for("dashboard"))
        else:
            return "<p>Invalid credentials. Go back and try again.</p>"
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("username", None)
    return redirect(url_for("index"))

@app.route("/dashboard")
def dashboard():
    if "username" not in session:
        return redirect(url_for("login"))

    username = session["username"]
    user_exercises = users[username]["exercises"]

    # First, compute the total "effective" minutes for each day
    daily_totals = {}
    for ex in user_exercises:
        # Parse date
        try:
            ex_date = datetime.datetime.strptime(ex["date"], "%Y-%m-%d").date()
        except ValueError:
            ex_date = None

        if ex_date:
            if ex["activity_type"] == "hike":
                # If hike and steps >= 10000 => 30 min
                if ex.get("steps", 0) >= 10000:
                    eff_min = 30
                else:
                    eff_min = 0
            else:
                eff_min = ex["time"]

            daily_totals[ex_date] = daily_totals.get(ex_date, 0) + eff_min

    # We'll create a new list that includes whether each row's date reached 30 min
    sorted_exercises = sorted(user_exercises, key=lambda x: x["date"], reverse=True)
    enhanced_exercises = []
    for ex in sorted_exercises:
        # By default, show 0 or 1 for daily point
        try:
            ex_date = datetime.datetime.strptime(ex["date"], "%Y-%m-%d").date()
        except ValueError:
            ex_date = None

        if ex_date and daily_totals.get(ex_date, 0) >= 30:
            daily_point = 1
        else:
            daily_point = 0

        new_ex = ex.copy()
        new_ex["daily_point"] = daily_point
        enhanced_exercises.append(new_ex)

    return render_template("dashboard.html",
                           username=username,
                           exercises=enhanced_exercises)

# @app.route("/add_exercise", methods=["POST"])
# def add_exercise():
#     if "username" not in session:
#         return redirect(url_for("login"))

#     global next_exercise_id

#     username = session["username"]
#     activity_type = request.form.get("activity_type", "others")
#     note = request.form.get("note", "").strip()

#     # Parse time
#     try:
#         time_val = int(request.form.get("time", "0"))
#     except:
#         time_val = 0

#     # Parse calories
#     try:
#         calorie_val = int(request.form.get("calorie", "0"))
#     except:
#         calorie_val = 0

#     # Parse steps
#     try:
#         steps_val = int(request.form.get("steps", "0"))
#     except:
#         steps_val = 0

#     date_str = request.form.get("date", "")

#     # If it's a hike, enforce steps >= 10000
#     if activity_type == "hike" and steps_val < 10000:
#         return "<p>If you select 'Hike', you must have at least 10,000 steps. <a href='/dashboard'>Back</a></p>"

#     exercise_entry = {
#         "id": next_exercise_id,
#         "activity_type": activity_type,
#         "note": note,
#         "time": time_val,
#         "calorie": calorie_val,
#         "steps": steps_val,
#         "date": date_str
#     }
#     next_exercise_id += 1

#     users[username]["exercises"].append(exercise_entry)
#     return redirect(url_for("dashboard"))

@app.route("/add_exercise", methods=["POST"])
def add_exercise():
    if "username" not in session:
        return redirect(url_for("login"))

    global next_exercise_id

    username = session["username"]
    activity_type = request.form.get("activity_type", "others")
    note = request.form.get("note", "").strip()

    try:
        time_val = int(request.form.get("time", "0"))
    except:
        time_val = 0

    try:
        calorie_val = int(request.form.get("calorie", "0"))
    except:
        calorie_val = 0

    try:
        steps_val = int(request.form.get("steps", "0"))
    except:
        steps_val = 0

    date_str = request.form.get("date", "")

    if activity_type == "hike" and steps_val < 10000:
        return "<p>If you select 'Hike', you must have at least 10,000 steps. <a href='/dashboard'>Back</a></p>"

    exercise_entry = {
        "id": next_exercise_id,
        "activity_type": activity_type,
        "note": note,
        "time": time_val,
        "calorie": calorie_val,
        "steps": steps_val,
        "date": date_str
    }
    next_exercise_id += 1

    users[username]["exercises"].append(exercise_entry)
    save_users(users)  # Save after adding exercise
    print("✅ User data saved successfully!") 
    return redirect(url_for("dashboard"))

# @app.route("/delete_exercise/<int:ex_id>", methods=["GET"])
# def delete_exercise(ex_id):
#     """
#     Delete the exercise with the given ID from the logged-in user's list.
#     """
#     if "username" not in session:
#         return redirect(url_for("login"))

#     username = session["username"]
#     new_list = []
#     for ex in users[username]["exercises"]:
#         if ex["id"] != ex_id:
#             new_list.append(ex)
#     users[username]["exercises"] = new_list
#     return redirect(url_for("dashboard"))

@app.route("/delete_exercise/<int:ex_id>", methods=["GET"])
def delete_exercise(ex_id):
    if "username" not in session:
        return redirect(url_for("login"))

    username = session["username"]
    new_list = [ex for ex in users[username]["exercises"] if ex["id"] != ex_id]
    users[username]["exercises"] = new_list
    save_users(users)  # Save after deletion
    return redirect(url_for("dashboard"))


@app.route("/scoreboard")
def scoreboard():
    results = []
    for u in users:
        pts = calculate_points_for_user(u)
        results.append({"username": u, "points": pts})
    results.sort(key=lambda x: x["points"], reverse=True)
    return render_template("scoreboard.html", scoreboard=results)

@app.route("/user/<username>")
def user_history(username):
    if username not in users:
        return f"<p>User '{username}' not found.</p>"
    sorted_exercises = sorted(users[username]["exercises"], key=lambda x: x["date"], reverse=True)
    return render_template("user_history.html",
                           viewed_user=username,
                           exercises=sorted_exercises)

if __name__ == "__main__":
    # For Replit or local usage
    app.run(host="0.0.0.0", port=81, debug=True)
