from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import google.generativeai as genai
from datetime import datetime, timedelta, timezone
import azure.cognitiveservices.speech as speechsdk
import random
import os
import pymongo
import bcrypt
import uuid 
import fitz 
import time
from flask_socketio import SocketIO, emit
import base64
from io import BytesIO
from PIL import Image
import cv2
import numpy as np
import mediapipe as mp
from google.cloud import storage  

app = Flask(__name__, template_folder="templates")
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.secret_key = "super_secret_key"
app.config["UPLOAD_FOLDER"] = "uploads"

GCS_BUCKET_NAME = "interview-uploads"

@app.route("/upload_video", methods=["POST"])
def upload_video():
    if "video" not in request.files:
        return jsonify({"error": "No video file received"}), 400

    video_file = request.files["video"]
    print("I got the video file.")
    unique_filename = f"interview_{int(time.time())}.webm"

    try:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "myinterviewvideos-c8174df6386d.json"
        client = storage.Client()
        bucket = client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(unique_filename)
        blob.upload_from_file(video_file, content_type="video/webm")

        print(" Video uploaded to GCS:", blob.public_url)
        return jsonify({"success": True, "video_url": blob.public_url})

    except Exception as e:
        print("GCS Upload Failed:", str(e))
        return jsonify({"error": str(e)}), 500

app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=30)  # Expires in 30 mins

#  Connect Flask to MongoDB
MONGO_URI = "mongodb+srv://123103054:TfUOHuLbpP5aONS6@cluster0.cssez.mongodb.net/interview_ai?retryWrites=true&w=majority&tls=true&tlsAllowInvalidCertificates=true"
mongo = pymongo.MongoClient(MONGO_URI)
db = mongo["interview_ai"]  # Database name

# Create Collections (like Tables in SQL)
users_collection = db["users"]
questions_collection = db["questions"]
interview_logs = db["interview_logs"]
sessions_collection = db["sessions"]
interviews_collection = db["interviews"]

# API Keys
GEMINI_API_KEY = "AIzaSyDIdfe_-YL7NRnSIshidt_ZJIBYIStXKyM"
SPEECH_KEY = "2IXedd2ndFNphFk2yosIclLJD7ziXm0eMIbjiJrWTyTV91p5kNFUJQQJ99BCACGhslBXJ3w3AAAYACOGM802"
SPEECH_REGION = "Centralindia"

# Configure Gemini AI
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.0-flash")

# 🎙️ Azure Speech SDK (STT/TTS)
speech_config = speechsdk.SpeechConfig(subscription=SPEECH_KEY, region=SPEECH_REGION)

# Predefined Main Questions
interview_questions = []

# Initialize global variables
question_index = 0
follow_up_remaining = 0
total_score = 0
total_questions = 0
candidate_responses = []

@app.route("/create_interview", methods=["POST"])
def create_interview():
    if "user" not in session or session["role"] != "company":
        return jsonify({"error": "Unauthorized"}), 403

    title = request.form.get("interview_title", "Untitled Interview")
    interview_id = f"I{random.randint(10000, 99999)}"

    interviews_collection.insert_one({
        "interview_id": interview_id,
        "interview_title": title,
        "company_username": session["user"],
        "created_at": datetime.utcnow()
    })

    # ✅ Redirect to the dashboard with interview_id
    return redirect(url_for("company_dashboard", interview_id=interview_id))

@app.route("/get_company_interviews", methods=["GET"])
def get_company_interviews():
    if "user" not in session or session["role"] != "company":
        return jsonify({"error": "Unauthorized"}), 403

    interviews_collection = db["interviews"]
    interviews = list(interviews_collection.find(
        {"company_username": session["user"]},
        {"_id": 0}
    ))

    return jsonify({"interviews": interviews})

@app.route("/dashboard_selector")
def dashboard_selector():
    if "user" not in session or session["role"] != "company":
        return redirect(url_for("home"))
    return render_template("select_interview.html")

@app.route("/get_company_user_id", methods=["GET"])
def get_company_user_id():
    """Fetch the company user ID for the logged-in user."""
    if "user" not in session or session["role"] != "company":
        return jsonify({"error": "Unauthorized"}), 403

    username = session["user"]
    
    # ✅ Fetch user details from MongoDB
    company_data = users_collection.find_one({"username": username}, {"_id": 0, "user_id": 1})

    if company_data and "user_id" in company_data:
        return jsonify({"user_id": company_data["user_id"]})
    else:
        return jsonify({"error": "User ID not found"}), 404

@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        if username in users and users[username]["password"] == password:
            session["user"] = username
            session["role"] = users[username]["role"]

            if users[username]["role"] == "candidate":
                return redirect(url_for("resume_upload"))
            elif users[username]["role"] == "company":
                return redirect(url_for("dashboard_selector"))
        
        return render_template("home.html", error="Invalid credentials")
    
    return render_template("home.html")

def extract_text_from_pdf(pdf_path):
    """Extracts text from a given PDF file."""
    text = ""
    with fitz.open(pdf_path) as doc:
        for page in doc:
            text += page.get_text("text")
    return text.strip()

@app.route("/resume_upload", methods=["GET", "POST"])
def resume_upload():
    """Resume upload page before interview starts."""
    if "user" not in session or session["role"] != "candidate":
        return redirect(url_for("home"))

    """Resume upload page before interview starts."""
    if "user" not in session or session["role"] != "candidate":
        return redirect(url_for("home"))

    username = session["user"]  # ✅ Define `username` at the beginning


    if request.method == "POST":
        job_desc = request.form.get("job_desc", "Software Development Engineer")
        resume = request.files["resume"]

        if resume and resume.filename.endswith(".pdf"):
            file_path = os.path.join(app.config["UPLOAD_FOLDER"], session["user"] + "_resume.pdf")
            resume.save(file_path)

            extracted_text = extract_text_from_pdf(file_path)

            prompt = f"""
            You are an experienced hiring manager & technical interviewer with extensive experience in screening resumes for Software Development Engineer (SDE) roles.

            Analyze the following resume in depth based on the given job description.

            Candidate Resume:
            {extracted_text}

            Rate this resume out of 10. Only generate a numerical answer between 0 to 10. Do not generate any other text than the score.
            """

            try:
                response = model.generate_content(prompt)
                resume_score = response.text.strip()
                if not resume_score.isdigit():
                    resume_score = "Error"
            except Exception as e:
                resume_score = "Error"

            # ✅ Store resume score in MongoDB
            interview_logs.update_one(
                {"username": username},
                {"$set": {"resume_score": resume_score}},  # Add `resume_score` field
                upsert=True  # Create document if it doesn't exist
            )

            interview_logs.update_one(
                {"username": username},
                {"$set": {"resume_text": extracted_text}},  # Add `resume_text` field
                upsert=True  # Create document if it doesn't exist
            )

            print(f"✅ Stored Resume Score for {username}: {resume_score}")

            return redirect(url_for("interview"))  # Proceed to interview

        return "Invalid file format. Please upload a PDF file."

    return render_template("resume_upload.html")

@app.route("/interview")
def interview():
    """Check session from MongoDB before granting interview access"""
    if "user" not in session:
        return redirect(url_for("home"))

    # ✅ Fetch session from MongoDB
    user_session = sessions_collection.find_one({"username": session["user"]})

    if not user_session or user_session["role"] != "candidate":
        return redirect(url_for("home"))

    return render_template("temp.html")

@app.route("/get_existing_questions", methods=["GET"])
def get_existing_questions():
    """Retrieves stored questions from the database for the logged-in user."""
    if "user" not in session:
        return jsonify({"error": "Unauthorized"}), 403

    username = session["user"]
    interview_id = session["interview_id"]
    entry = questions_collection.find_one({"interview_id": interview_id}, {"_id": 0, "questions": 1})
    if entry:
        return jsonify({"questions": entry["questions"]})
    else:
        return jsonify({"questions": []})



@app.route("/save_question", methods=["POST"])
def save_question():
    """Saves selected questions to the database."""
    if "user" not in session:
        return jsonify({"error": "Unauthorized"}), 403

    username = session["user"]
    interview_id = session["interview_id"]
    data = request.get_json()
    question_text = data.get("question", "").strip()

    if not question_text:
        return jsonify({"error": "Invalid question!"}), 400

    existing = questions_collection.find_one({"interview_id": interview_id})
    if existing:
        if question_text not in existing["questions"]:
            questions_collection.update_one(
                {"interview_id": interview_id},
                {"$push": {"questions": question_text}}
            )
    else:
        questions_collection.insert_one({"interview_id": interview_id, "questions": [question_text]})

    return jsonify({"success": True})

@app.route("/company_dashboard/<interview_id>")
def company_dashboard(interview_id):
    if "user" not in session or session["role"] != "company":
        return redirect(url_for("home"))

    # Store selected interview in session for use in other routes
    session["interview_id"] = interview_id

    return render_template("company_dashboard.html", interview_id=interview_id)

# 🤖 AI Generates Questions Based on Job Title
@app.route("/generate_questions", methods=["POST"])
def generate_questions():
    """AI generates 5 interview questions based on the provided job title."""
    data = request.get_json()
    job_title = data.get("job_title", "")
    difficulty = data.get("difficulty", "Medium")
    min_exp = data.get("min_exp", "No experience")
    num_questions = int(data.get("num_questions", 10)) 

    if not job_title:
        return jsonify({"error": "Job title is required!"}), 400

     # Ensure num_questions is within a valid range
    num_questions = max(1, min(num_questions, 100))  

    prompt = f"""Generate {num_questions} technical and one line interview questions for the job title:{job_title}and difficulty is {difficulty}.. Give each question in different line.Meaning of difficulty -(If the difficulty is Easy, make questions more theoretical and basic. If Medium, include problem-solving and coding-related questions. If Hard, include complex problem-solving, system design, and case studies). Give questions of selected difficulty only. Do not give any other text than five questions."""
    ai_response = model.generate_content(prompt)
    
    # ✅ Extract questions (AI might return text, split into separate questions)
    generated_questions = ai_response.text.strip().split("\n")

    # ✅ Store the generated questions in session
    session["generated_questions"] = generated_questions[:num_questions]

    return jsonify({"questions": generated_questions[:num_questions]})  # Return only 5 questions

# 📌 **Route: Logout**
@app.route("/logout")
def logout():

    if "user" in session:
        # ✅ Delete the session from MongoDB
        sessions_collection.delete_one({"username": session["user"]})

    session.clear()
    return redirect(url_for("home"))


# 🎤 Speech-to-Text (Azure STT)
# @app.route("/speech_to_text", methods=["POST"])
# def recognize_speech_from_mic():
#     """Capture user speech and convert it to text using Azure STT."""
#     try:
#         speech_recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config)
#         speech_config.speech_synthesis_voice_name = "en-IN-AashiNeural"

#         print("Listening for speech...")
#         result = speech_recognizer.recognize_once()

#         if result.reason == speechsdk.ResultReason.RecognizedSpeech:
#             return jsonify({"text": result.text})
#         elif result.reason == speechsdk.ResultReason.NoMatch:
#             return jsonify({"text": "No speech recognized."})
#         else:
#             return jsonify({"text": "Speech recognition failed."})
#     except Exception as e:
#         return jsonify({"error": str(e)})


# 📊 Evaluate Response
def evaluate_response():
    """Evaluates the final interview score based on all questions and responses."""
    username = session["user"]

    # Retrieve all stored questions and responses
    logs = interview_logs.find_one({"username": username}, {"_id": 0, "logs": 1})

    if not logs or "logs" not in logs:
        return jsonify({"error": "No interview data found"}), 400

    completed_logs = [log for log in logs["logs"] if log["response"]]

    if not completed_logs:
        return jsonify({"error": "No completed responses available for evaluation"}), 400

    # Format questions & responses for AI evaluation
    interview_text = "\n".join([f"Q: {log['question']}\nA: {log['response']}" for log in completed_logs])

    # AI Evaluation Prompt
    prompt = f"""Analyze the following interview responses and provide a final score (0-10) :

    {interview_text}

    give a int value only no other text than that.
    """

    ai_response = model.generate_content(prompt)

    try:
        score_text = ai_response.text.strip()
        score = int(score_text)

        if 0 <= score <= 10:
            total_score = score
        else:
            raise ValueError("Invalid AI score")  # Handle invalid numbers

    except (ValueError, AttributeError):
        total_score = 5  # Default score if AI response is unclear

    # Store final score and feedback in database
    interview_logs.update_one(
        {"username": username},
        {"$set": {"final_score": total_score}}
    )

    return total_score

# 🔊 Text-to-Speech (TTS)
@app.route("/text_to_speech", methods=["POST"])
def text_to_speech():
    """Convert text to speech using Azure Speech SDK and send the audio file to the browser."""
    data = request.get_json()
    text = data.get("text", "")

    if not text:
        return jsonify({"error": "No text provided"}), 400

    try:
        file_path = "static/output.mp3"

        # ✅ Delete the old file to ensure a fresh file is created
        if os.path.exists(file_path):
            os.remove(file_path)

        # Configure speech synthesizer to write to a file instead of playing locally
        audio_config = speechsdk.audio.AudioOutputConfig(filename=file_path)
        synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)
        speech_config.speech_synthesis_voice_name = "en-IN-AashiNeural"
        result = synthesizer.speak_text_async(text).get()

        if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
            print(f"✅ New TTS Audio File Created: {file_path}")  # Debug log
            return jsonify({"audio_url": "/" + file_path})  # Send file URL
        else:
            print(f"❌ TTS Failed: {result.reason}")  # Debug log
            return jsonify({"error": "TTS failed", "reason": str(result.reason)}), 500

    except Exception as e:
        print(f"❌ Error in TTS: {str(e)}")  # Debug log
        return jsonify({"error": str(e)}), 500

# 📌 **Serve Static Audio File in Flask**
@app.route("/static/<path:filename>")
def serve_audio(filename):
    """Serve the generated audio file to the client."""
    return send_from_directory("static", filename)

# 🎬 **Start Interview**
@app.route("/start_interview", methods=["GET"])
def start_interview():
    """Resets all variables and starts a fresh interview."""
    global question_index, follow_up_remaining, total_score, total_questions, candidate_responses

    if "user" not in session:
        return jsonify({"error": "Unauthorized"}), 403

    username = session["user"]

    # ✅ Ensure `logs` array exists for this user
    interview_logs.update_one(
        {"username": username},
        {"$setOnInsert": {"logs": []}},  # ✅ Creates `logs` array if it does not exist
        upsert=True
    )

    # ✅ Fetch candidate data from `interview_logs`
    candidate_data = interview_logs.find_one({"username": username}, {"_id": 0, "resume_score": 1})

    if candidate_data and "resume_score" in candidate_data:
        resume_score = candidate_data["resume_score"]
    else:
        resume_score = "Not available"

    print(f"✅ Resume Score for {username}: {resume_score}")

    # ✅ Clear old interview logs for this user
    interview_logs.update_one(
        {"username": username},
        {"$set": {"logs": [], "resume_score": resume_score}},  # Ensure `resume_score` is included
        upsert=True
    )
    
     # ✅ Store the resume score in session for later retrieval
    session["resume_score"] = resume_score

    question_index = 0
    follow_up_remaining = 0
    total_score = 0
    total_questions = 0
    candidate_responses = []

    open("interview_log.txt", "w").close()
    return get_question()  # Immediately send the first question

# 🤖 Get Next Question
@app.route("/get_question", methods=["GET"])
def get_question():
    global question_index, follow_up_remaining, total_score, total_questions, candidate_responses

    if "user" not in session or "company_name" not in session:
        print("🔴 ERROR: User session or company_name missing!")
        return jsonify({"error": "Unauthorized"}), 403

    username = session["user"]
    
    interview_id = session["interview_id"]
    company_data = questions_collection.find_one({"interview_id": interview_id}, {"_id": 0, "questions": 1})

    all_questions = company_data["questions"] if company_data else []

    if not all_questions:
        return jsonify({"error": "No questions available for this company!"})
    
    # ✅ Select 2 random questions at the start of the interview
    if "selected_questions" not in session:
        session["selected_questions"] = random.sample(all_questions, min(1, len(all_questions)))

    selected_questions = session["selected_questions"]  # Use only these 2 questions

    # If the interview is starting, reset tracking variables
    if request.args.get("start") == "true":
        question_index = 0
        follow_up_remaining = 0
        candidate_responses = []
        return jsonify({"question": "Interview started! Here is your first question:", "type": "start"})

    # If interview is over
    if question_index >= len(selected_questions):
        final_score = evaluate_response()
        resume_score = session.get("resume_score", "Not available")
        
        overall_score = (0.4 * float(resume_score)) + (0.6 * float(final_score))

        interview_logs.update_one(
            {"username": username},
            {"$set": {"overall_score": overall_score}},  # Ensure `resume_score` is included
            upsert=True
        )

        return jsonify({
            "message": "Interview Completed!",
            "score": final_score,
            "resume_score": resume_score,
            "overall_score": overall_score
        })
    
       
    # Select the current question from the list
    current_question = selected_questions[question_index]

    if follow_up_remaining == 0:
        follow_up_remaining = 1  # Set follow-up question count

        with open("interview_log.txt", "a", encoding="utf-8") as log_file:
            log_file.write(f"Q: {current_question}\n\n")

        # ✅ Store the asked question in MongoDB (without response yet)
        interview_logs.update_one(
            {"username": username},
            {
                "$push": {
                    "logs": {
                        "question": current_question,
                        "response": None,
                        "timestamp": datetime.now(timezone.utc)
                    }
                }
            },
            upsert=True  # ✅ Creates a new document if user logs don’t exist
        )

        return jsonify({"question": current_question, "type": "main"})

    # Generate a contextual follow-up question
    previous_answers = " ".join(candidate_responses[-1:]) or "No previous responses yet."
    prompt = (
        f"The candidate previously responded: '{previous_answers}'. "
        f"Ask a natural, engaging one-line follow-up question, avoiding robotic phrasing. Sound more like human is asking. and do not give any other text then question and avoid bold."
    )
    follow_up_question = model.generate_content(prompt).text.strip()

    with open("interview_log.txt", "a", encoding="utf-8") as log_file:
        log_file.write(f"Q: {follow_up_question}\n\n")

    # ✅ Store the follow-up question in MongoDB
    interview_logs.update_one(
        {"username": username},
        {
            "$push": {
                "logs": {
                    "question": follow_up_question,
                    "response": None,
                    "timestamp": datetime.now(timezone.utc)
                }
            }
        },
        upsert=True
    )

    follow_up_remaining -= 1  # Decrement follow-ups left

    if follow_up_remaining == 0:
        question_index += 1  # Move to the next main question

    return jsonify({"question": follow_up_question, "type": "follow-up"})

# 📩 Submit Candidate Response
@app.route("/submit_response", methods=["POST"])
def submit_response():
    """Stores candidate responses inside the MongoDB user's logs array"""
    global candidate_responses

    if "user" not in session:
        return jsonify({"error": "Unauthorized"}), 403

    username = session["user"]
    data = request.get_json()
    user_response = data.get("response", "")

    # ✅ Append response to candidate_responses list
    candidate_responses.append(user_response)

    with open("interview_log.txt", "a", encoding="utf-8") as log_file:
        log_file.write(f"A: {user_response}\n\n")

    # ✅ Find the latest unanswered question for this user
    latest_question = interview_logs.find_one(
        {"username": username, "logs.response": None},  # Look for the latest unanswered question
        {"logs.$": 1}  # Retrieve only the first matching log entry
    )

    if latest_question and "logs" in latest_question:
       # ✅ Update the latest question with the response
        interview_logs.update_one(
            {"username": username, "logs.question": latest_question["logs"][0]["question"]},
            {"$set": {"logs.$.response": user_response}}
        )
        return jsonify({"success": True, "message": "Response recorded successfully."})

    return jsonify({"error": "No question found to update"}), 400

@app.route("/get_feedback", methods=["GET"])
def get_feedback():
    """Retrieves all stored questions & responses for feedback analysis"""
    if "user" not in session:
        return jsonify({"error": "Unauthorized"}), 403

    username = session["user"]

    # ✅ Fetch the user's interview logs (only completed responses)
    logs = interview_logs.find_one({"username": username}, {"_id": 0, "logs": 1})

    if not logs or "logs" not in logs:
        return jsonify({"error": "No interview data found"}), 400

    # ✅ Filter out questions without responses
    completed_logs = [log for log in logs["logs"] if log["response"]]

    if not completed_logs:
        return jsonify({"error": "No completed responses available for feedback"}), 400

    # ✅ Create a formatted interview transcript for AI evaluation
    interview_text = "\n".join([f"Q: {log['question']}\nA: {log['response']}" for log in completed_logs])

    # ✅ Construct AI prompt for feedback
    prompt = f"""Analyze the following interview and provide feedback:
    Interview Transcript:
    {interview_text}

    Please evaluate and provide:
    - Overall performance
    - Strengths
    - Areas for improvement
    
    give a score out of 10 based on this -
    3 marks on how much are these answers related to questions.
    3 marks for how correct are comunication skills.
    4 marks for how correct answer technically is.

    give simple text in this feedback and avoid bold.
    """

    # ✅ Generate feedback using AI (Gemini)
    ai_response = model.generate_content(prompt)

    return jsonify({"feedback": ai_response.text.strip()})

def generate_unique_user_id():
    """Generates a unique 5-digit user ID and ensures it does not already exist in the database."""
    while True:
        user_id = str(random.randint(10000, 99999))  # ✅ Generate a random 5-digit number
        if not users_collection.find_one({"user_id": user_id}):  # ✅ Check uniqueness in DB
            return user_id  # ✅ Return the unique ID

@app.route("/register", methods=["POST"])
def register():
    """Registers a new user (Candidate or Company)"""
    new_username = request.form["new_username"]
    new_password = request.form["new_password"]
    role = request.form["role"]

    if users_collection.find_one({"username": new_username}):
        return "Username already exists!", 400

    # Generate a unique user ID
    user_id = generate_unique_user_id()  # Generates a unique identifier

    # Hash password for security
    hashed_password = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt())

    # Insert user into the database
    users_collection.insert_one({
        "user_id": user_id,  # Store the generated unique ID
        "username": new_username,
        "password": hashed_password,
        "role": role
    })
    return redirect(url_for("home"))

@app.route("/login_candidate", methods=["POST"])
def login_candidate():
    """Handles candidate login using interview ID"""
    username = request.form["username"]
    password = request.form["password"]
    interview_id = request.form["interview_id"].strip()

    user = users_collection.find_one({"username": username, "role": "candidate"})

    if user and bcrypt.checkpw(password.encode("utf-8"), user["password"]):
        interview = interviews_collection.find_one({"interview_id": interview_id})
        if not interview:
            return "Invalid Interview ID", 400

        company_username = interview["company_username"]

        # ✅ Store session info
        session["user"] = username
        session["role"] = "candidate"
        session["company_name"] = company_username
        session["interview_id"] = interview_id

        sessions_collection.delete_many({"username": username})
        sessions_collection.insert_one({
            "username": username,
            "role": "candidate",
            "company_name": company_username,
            "interview_id": interview_id,
            "ip_address": request.remote_addr,
            "user_agent": request.headers.get("User-Agent"),
            "login_time": datetime.now(timezone.utc),
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=1)
        })

        return redirect(url_for("resume_upload"))

    return "Invalid candidate credentials", 400

@app.route("/login_company", methods=["POST"])
def login_company():
    """Handles company login"""
    username = request.form["username"]
    password = request.form["password"]

    # ✅ Find the company user in MongoDB
    user = users_collection.find_one({"username": username, "role": "company"})

    # ✅ Verify password using bcrypt
    if user and bcrypt.checkpw(password.encode("utf-8"), user["password"]):
        session["user"] = username
        session["role"] = "company"

        # ✅ Remove old session and store new session
        sessions_collection.delete_many({"username": username})
        session_data = {
            "username": username,
            "role": "company",
            "ip_address": request.remote_addr,
            "user_agent": request.headers.get("User-Agent"),
            "login_time": datetime.now(timezone.utc),
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=1)
        }
        sessions_collection.insert_one(session_data)

        return redirect(url_for("dashboard_selector"))  

    return "Invalid company credentials", 400

@app.route("/active_sessions")
def active_sessions():
    """Returns a list of active user sessions from MongoDB"""
    # ✅ Auto-remove expired sessions
    sessions_collection.delete_many({"expires_at": {"$lt": datetime.utcnow()}})

    # ✅ Fetch active sessions
    active_users = list(sessions_collection.find({}, {"_id": 0}))
    return jsonify(active_users)

@app.route("/get_interview_logs", methods=["GET"])
def get_interview_logs():
    """Retrieves all stored questions & responses for the logged-in user"""
    if "user" not in session:
        return jsonify({"error": "Unauthorized"}), 403

    username = session["user"]

    # ✅ Fetch the user's interview logs
    logs = interview_logs.find_one({"username": username}, {"_id": 0, "logs": 1})

    if not logs:
        return jsonify({"logs": []})  # Return empty logs if none exist

    return jsonify({"logs": logs["logs"]})

@app.route("/delete_question", methods=["POST"])
def delete_question():
    """Deletes a question from the company's question list."""
    data = request.get_json()
    question_text = data.get("question", "")

    if not question_text:
        return jsonify({"error": "Invalid question!"}), 400

    # ✅ Remove the question from the company's array
    result = questions_collection.update_one(
        {"company": session["user"]},  # Identify the company
        {"$pull": {"questions": question_text}}  # Remove only this question
    )

    if result.modified_count > 0:
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "error": "Question not found!"})

if __name__ == "__main__":
    app.run(debug=True)

