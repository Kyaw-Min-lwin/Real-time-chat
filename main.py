from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
)
from flask_mysqldb import MySQL
import MySQLdb.cursors
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import os
import re
from time import time
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask_socketio import SocketIO, emit, join_room, leave_room
import uuid
from PIL import Image


app = Flask(__name__)
app.secret_key = "zt64kC2VVk"
socketio = SocketIO(app)

# mysql configuration
app.config["MYSQL_HOST"] = "localhost"
app.config["MYSQL_USER"] = "root"
app.config["MYSQL_PASSWORD"] = "idkSth1*"
app.config["MYSQL_DB"] = "realtimechat"

mysql = MySQL(app)


# Rate limiting configuration
limiter = Limiter(
    key_func=get_remote_address
)
limiter.init_app(app)
messages_timestamps = {}

# File upload configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif"}


def allowed_file(filename):
    print("File allowed?", filename)
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route("/")
def index():
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute("SELECT * FROM chat_groups")
    chats = cursor.fetchall()
    return render_template("index.html", chats=chats)


@app.route("/login/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]
        cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
        cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()

        if user and check_password_hash(user["password"], password):
            session["loggedin"] = True
            session["id"] = user["id"]
            session["name"] = user["name"]
            session["role"] = user["role"]
            flash("Login successful!", "success")
            return redirect(url_for("index"))
        else:
            flash("Invalid email or password!", "danger")
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email")
        password = request.form.get("password")
        confirm = request.form.get("confirm")

        cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
        cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
        account = cursor.fetchone()

        if account:
            flash("Account already exists!", "danger")
        elif not re.match(r"[^@]+@[^@]+\.[^@]+", email):
            flash("Invalid email address!", "danger")
        elif password != confirm:
            flash("Passwords do not match!", "danger")
        else:
            hashed_password = generate_password_hash(password)
            cursor.execute(
                "INSERT INTO users (name, email, password) VALUES (%s, %s, %s)",
                (name, email, hashed_password),
            )
            mysql.connection.commit()
            flash("Registration successful!", "success")
            return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/logout")
def logout():
    if "loggedin" in session:
        session.clear()
        flash("You have been logged out!", "success")
        return redirect(url_for("login"))

    else:
        flash("You are not logged in!", "danger")
        return redirect(url_for("login"))


@app.route("/create_group", methods=["GET", "POST"])
@limiter.limit("3 per minute")
def create_group():
    # Check if the user is logged in
    if "loggedin" not in session:
        flash("You must be logged in to create group!", "danger")
        return redirect(url_for("login"))

    if request.method == "POST":
        # Get form data
        group_name = request.form.get("group_name")
        description = request.form.get("description")
        privacy = request.form.get("privacy")
        access_code = request.form.get("access_code")
        image = request.files.get("group_image")

        # Validate required fields
        if not group_name or not description or not privacy:
            flash("Group name, description, and privacy fields are required!", "danger")
            return redirect(url_for("create_group"))

        # Handle image upload
        if image and image.filename != "":
            if allowed_file(image.filename):
                # Generate a secure, unique filename
                filename = secure_filename(image.filename)
                ext = filename.rsplit(".", 1)[1].lower()
                unique_filename = f"{uuid.uuid4().hex}.{ext}"
                image_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_filename)

                try:
                    # Open and resize image using Pillow
                    img = Image.open(image)
                    max_size = (500, 500)
                    img.thumbnail(max_size)

                    # Save optimized image
                    img.save(image_path, optimize=True, quality=85)
                except Exception as e:
                    print("Image save failed:", e)

                # Store relative URL for database
                image_url = f"/static/uploads/{unique_filename}"
            else:
                flash("Invalid image file!", "danger")
                return redirect(url_for("create_group"))
        else:
            # Use default image if none uploaded
            filename = "default.png"
            image_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
            image_url = f"/static/uploads/{filename}"

        # Convert privacy to boolean
        privacy = privacy == "private"

        # Validate access code for private groups
        if privacy and not access_code:
            flash("Access code is required for private groups.", "danger")
            return redirect(url_for("create_group"))

        # Insert group into database
        cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
        cursor.execute(
            "INSERT INTO chat_groups (title, description, image_url, isprivate, access_code) VALUES (%s, %s, %s, %s, %s)",
            (
                group_name,
                description,
                image_url,
                privacy,
                access_code if privacy else None,
            ),
        )
        mysql.connection.commit()

        flash("Group created successfully!", "success")
        return redirect(url_for("index"))

    # If not POST, redirect to home
    return redirect(url_for("index"))


@app.route("/search_groups")
def search_groups():
    name = request.args.get("q", "").strip()
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute(
        "SELECT id, title, isprivate FROM chat_groups WHERE title LIKE %s",
        ("%" + name + "%",),
    )
    groups = cursor.fetchall()
    return jsonify(groups)


@socketio.on("join")
def handle_join(data):
    room = str(data["group_id"])
    join_room(room)
    emit("status", {"msg": f"{data['username']} has entered the room."}, room=room)


@socketio.on("send_message")
def handle_message(data):
    user_id = data["user_id"]
    content = data["content"]
    group_id = str(data["group_id"])
    sender_name = data["sender_name"]

    now = time()

    timestamps = messages_timestamps.get(user_id, [])

    timestamps = [ts for ts in timestamps if now - ts < 60]
    # Remove timestamps older than 60 seconds
    if len(timestamps) >= 10:
        emit("error", {"msg": "Rate limit exceeded. Max 10 messages per 60 seconds."})
        return
    timestamps.append(now)
    messages_timestamps[user_id] = timestamps

    # Save message to database
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute(
        "INSERT INTO messages (group_id, user_id, content) VALUES (%s, %s, %s)",
        (group_id, user_id, content),
    )
    mysql.connection.commit()

    # Broadcast message to room
    emit(
        "new_message",
        {
            "group_id": group_id,
            "user_id": user_id,
            "content": content,
            "sender_name": sender_name,
        },
        room=group_id,
    )


@app.route("/join_group", methods=["POST"])
def join_group():
    group_id = request.form.get("group_id")
    access_code = request.form.get("access_code2")

    # Validate group ID
    if not group_id:
        flash("Please select a group.", "danger")
        return redirect(url_for("index"))

    group_id = int(group_id)

    # Fetch group from DB
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute("SELECT * FROM chat_groups WHERE id = %s", (group_id,))
    group = cursor.fetchone()

    if not group:
        flash("Group not found.", "danger")
        return redirect(url_for("index"))

    # Check access code for private groups
    if group["isprivate"]:
        if not access_code or access_code != group["access_code"]:
            flash("Access code is incorrect.", "danger")
            return redirect(url_for("index"))

    # Ensure 'joined_groups' exists in session
    if "joined_groups" not in session:
        session["joined_groups"] = []

    joined = session["joined_groups"]

    # If already joined, skip insertion and redirect
    if str(group_id) in joined:
        cursor.execute(
            """
            SELECT u.id, u.name
            FROM group_membership gm
            JOIN users u ON gm.user_id = u.id
            WHERE gm.group_id = %s
        """,
            (group_id,),
        )
        group_members = cursor.fetchall()
        return redirect(url_for("view_group", group_id=group_id, members=group_members))

    # Add to session group list
    joined.append(str(group_id))
    session["joined_groups"] = joined

    # Insert into group_membership
    cursor.execute(
        """
        INSERT INTO group_membership (group_id, user_id)
        VALUES (%s, %s)
    """,
        (group_id, session["id"]),
    )
    mysql.connection.commit()

    # Fetch group members
    cursor.execute(
        """
        SELECT u.id, u.name
        FROM group_membership gm
        JOIN users u ON gm.user_id = u.id
        WHERE gm.group_id = %s
    """,
        (group_id,),
    )
    group_members = cursor.fetchall()

    # Redirect to group page
    return redirect(url_for("view_group", group_id=group_id, members=group_members))


@app.route("/group/<int:group_id>")
def view_group(group_id):
    if "loggedin" not in session:
        flash("You must be logged in to view groups.", "danger")
        return redirect(url_for("login"))

    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute("SELECT * FROM chat_groups WHERE id = %s", (group_id,))
    group = cursor.fetchone()
    cursor.execute(
        "SELECT u.id, u.name FROM group_membership gm JOIN users u ON gm.user_id = u.id WHERE gm.group_id = %s",
        (group_id,),
    )
    group_members = cursor.fetchall()

    if not group:
        flash("Group not found.", "danger")
        return redirect(url_for("index"))

    # Access control for private groups
    if group["isprivate"]:
        joined = session.get("joined_groups", [])
        if str(group_id) not in map(
            str, joined
        ):  # ensure comparison works with both int/str
            flash("You must join this private group before accessing it.", "warning")
            return redirect(url_for("index"))

    messages_cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    messages_cursor.execute(
        """
    SELECT m.*, u.name AS sender_name
    FROM messages m
    JOIN users u ON m.user_id = u.id
    WHERE m.group_id = %s
    ORDER BY m.updated_at ASC
""",
        (group_id,),
    )
    messages = messages_cursor.fetchall()

    return render_template(
        "view_group.html", group=group, messages=messages, group_members=group_members
    )


@app.route("/leave_group", methods=["POST"])
def leave_group():
    if "id" not in session:
        flash("You must be logged in to leave a group.", "danger")
        return redirect(url_for("login"))

    user_id = session["id"]
    group_id = request.form.get("group_id")

    if not group_id:
        flash("Invalid group.", "danger")
        return redirect(url_for("index"))

    group_id = int(group_id)

    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    # Remove from group_membership
    cursor.execute(
        "DELETE FROM group_membership WHERE group_id = %s AND user_id = %s",
        (group_id, user_id),
    )
    mysql.connection.commit()

    # Remove from session joined_groups
    if "joined_groups" in session:
        joined = session["joined_groups"]
        if str(group_id) in joined:
            joined.remove(str(group_id))
            session["joined_groups"] = joined

    flash("You have left the group.", "info")
    return redirect(url_for("index"))

@socketio.on("leave")
def handle_leave(data):
    room = str(data["group_id"])
    leave_room(room)
    emit("status", {"msg": f"{data['username']} has left the room."}, room=room)

if __name__ == "__main__":
    app.run(debug=True)
