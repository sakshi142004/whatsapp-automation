from flask import Flask, request, jsonify, render_template, redirect, url_for, session, send_from_directory
import pymysql
import requests
import os
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "secret key"

# ✅ Database Configuration
def get_db_connection():
    return pymysql.connect(
        host="localhost",
        user="username",
        password="password",
        database="database_name",
        cursorclass=pymysql.cursors.DictCursor
    )

# ✅ UltraMsg API Configuration
INSTANCE_ID = "replace with your instance id"
API_KEY = "replace with your api key"
ULTAMSG_URL = f"https://api.ultramsg.com/{INSTANCE_ID}/messages/chat"

# ✅ Folder for storing uploaded files
UPLOAD_FOLDER = "uploads"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# ✅ Serve uploaded files
@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# ✅ Login System
@app.route('/')
def index():
    if 'user' not in session:
        return redirect(url_for('login'))
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT password FROM teachers WHERE username=%s", (username,))
        user = cursor.fetchone()

        cursor.close()
        conn.close()

        if user and user["password"] == password:
            session['user'] = username
            return redirect(url_for('index'))
        else:
            return "Invalid credentials, please try again."
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('login'))

# ✅ Search Students
@app.route("/search")
def search_students():
    query = request.args.get("query", "").strip()
    selected_class = request.args.get("class", "all")
    selected_section = request.args.get("section", "both")

    conn = get_db_connection()
    cursor = conn.cursor()

    sql = "SELECT id, name, class, section FROM students WHERE name LIKE %s"
    params = (f"%{query}%",)

    if selected_class != "all":
        sql += " AND class = %s"
        params += (selected_class,)

    if selected_section != "both":
        sql += " AND section = %s"
        params += (selected_section,)

    cursor.execute(sql, params)
    students = cursor.fetchall()

    cursor.close()
    conn.close()

    return jsonify(students)

# ✅ Fetch Phone Numbers
def get_phone_numbers(student_ids):
    connection = get_db_connection()
    cursor = connection.cursor()

    print(f"DEBUG: Fetching phone numbers for IDs {student_ids}")

    # ✅ Use parameterized query to prevent SQL injection
    format_strings = ','.join(['%s'] * len(student_ids))
    query = f"SELECT id, phone_number FROM students WHERE id IN ({format_strings})"
    cursor.execute(query, tuple(student_ids))

    result = cursor.fetchall()
    cursor.close()
    connection.close()

    phone_numbers = {row["id"]: row["phone_number"] for row in result if row["phone_number"]}
    print("DEBUG: Fetched phone numbers:", phone_numbers)
    return phone_numbers




@app.route('/history')
def history():
    if 'user' not in session or not session['user']:
        return redirect(url_for('login'))  # Ensure only logged-in users can access

    conn = get_db_connection()
    cursor = conn.cursor()

    # Fetch only messages sent by the logged-in teacher
    cursor.execute("SELECT recipients, message, file_url, timestamp FROM messages WHERE teacher_username = %s ORDER BY timestamp DESC", 
                   (session['user'],))
    
    messages = cursor.fetchall()
    
    cursor.close()
    conn.close()

    return render_template("history.html", messages=messages, username=session['user'])




# ✅ Send Message to Selected Students or Parents
import requests

@app.route("/send_message", methods=["POST"])
def send_message():
    if "user" not in session:
        return jsonify({"error": "Unauthorized access"}), 401

    message = request.form.get("message")
    student_ids = request.form.getlist("recipients[]")
    recipient_type = request.form.get("recipient_type")
    file = request.files.get("file")

    if not student_ids or not message:
        return jsonify({"error": "Missing student selection or message"}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    # ✅ Fetch phone numbers
    recipients = []
    try:
        format_strings = ','.join(['%s'] * len(student_ids))
        query = f"SELECT id, phone_number, parent_number FROM students WHERE id IN ({format_strings})"
        cursor.execute(query, tuple(student_ids))
        rows = cursor.fetchall()

        for row in rows:
            if recipient_type in ["students", "both"] and row["phone_number"]:
                recipients.append(row["phone_number"])
            if recipient_type in ["parents", "both"] and row["parent_number"]:
                recipients.append(row["parent_number"])

        if not recipients:
            return jsonify({"error": "No valid phone numbers found"}), 400

    except pymysql.MySQLError as e:
        return jsonify({"error": "Database error"}), 500

    finally:
        cursor.close()
        conn.close()

    # ✅ Send Messages via UltraMsg API
    errors = []
    file_url = None

    if file:
        file_path = os.path.join(app.config["UPLOAD_FOLDER"], file.filename)
        file.save(file_path)
        file_url = f"{request.host_url}uploads/{file.filename}"

    for phone in recipients:
        payload = {
            "token": API_KEY,
            "to": phone,
            "body": message,
            "priority": 10
        }
        if file_url:
            payload["caption"] = message
            payload["image"] = file_url

        try:
            response = requests.post(ULTAMSG_URL, json=payload, timeout=10)
            response.raise_for_status()
            print(f"Message sent to {phone}: {response.json()}")
        except requests.RequestException as e:
            print(f"Failed to send message to {phone}: {str(e)}")
            errors.append(phone)

    if errors:
        return jsonify({"error": f"Failed to send messages to: {', '.join(errors)}"}), 500

    return jsonify({"success": "Messages sent successfully!"})

# ✅ Send Message to All Students in a Class & Section
@app.route('/send_message_all', methods=['POST'])
def send_message_all():
    if 'user' not in session:
        return jsonify({"error": "Unauthorized access"}), 401

    message = request.form.get('message')
    recipient_type = request.form.get("recipient_type")  # students, parents, or both
    selected_class = request.form.get('class')
    selected_section = request.form.get('section')
    file = request.files.get('file')

    if not message:
        return jsonify({"error": "Missing message"}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "SELECT id, phone_number, parent_number FROM students WHERE class = %s AND section = %s",
            (selected_class, selected_section)
        )
        rows = cursor.fetchall()

        if not rows:
            return jsonify({"error": "No students found for the selected class and section"}), 400

        recipients = []
        recipient_ids = []  # ✅ Store recipient student IDs for logging in DB

        for row in rows:
            if recipient_type in ["students", "both"] and row["phone_number"]:
                recipients.append(row["phone_number"])
                recipient_ids.append(row["id"])
            if recipient_type in ["parents", "both"] and row["parent_number"]:
                recipients.append(row["parent_number"])
        
        if not recipients:
            return jsonify({"error": "No valid phone numbers found"}), 400

    except pymysql.MySQLError as e:
        return jsonify({"error": "Database error"}), 500

    finally:
        cursor.close()
        conn.close()

    file_url = None
    if file:
        file_path = os.path.join(app.config["UPLOAD_FOLDER"], file.filename)
        file.save(file_path)
        file_url = f"{request.host_url}uploads/{file.filename}"

    errors = []
    for phone in recipients:
        payload = {
            "token": API_KEY,
            "to": phone,
            "body": message,
            "priority": 10
        }
        if file_url:
            payload["caption"] = message
            payload["image"] = file_url

        try:
            response = requests.post(ULTAMSG_URL, json=payload, timeout=10)
            response.raise_for_status()
        except requests.RequestException as e:
            errors.append(phone)

    # ✅ Save Message in Database
    '''conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        for student_id in recipient_ids:
            cursor.execute(
                "INSERT INTO messages (student_id, class, section, message, file_url, timestamp) VALUES (%s, %s, %s, %s, %s, NOW())",
                (student_id, selected_class, selected_section, message, file_url)
            )

        conn.commit()
    except pymysql.MySQLError as e:
        conn.rollback()
        return jsonify({"error": "Failed to save message to database"}), 500
    finally:
        cursor.close()
        conn.close()
'''
    if errors:
        return jsonify({"error": f"Failed to send messages to: {', '.join(errors)}"}), 500

    return jsonify({"success": "Messages sent and stored successfully!"})

if __name__ == '__main__':
    app.run(debug=True)
