from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import google.generativeai as genai
import azure.cognitiveservices.speech as speechsdk
import random
import os


app = Flask(__name__, template_folder="templates")
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.secret_key = "super_secret_key"

# 🔐 API Keys
GEMINI_API_KEY = "AIzaSyDIdfe_-YL7NRnSIshidt_ZJIBYIStXKyM"
SPEECH_KEY = "2IXedd2ndFNphFk2yosIclLJD7ziXm0eMIbjiJrWTyTV91p5kNFUJQQJ99BCACGhslBXJ3w3AAAYACOGM802"
SPEECH_REGION = "Centralindia"

# demo users
users = {
    "candidate": {"password": "candidate123", "role": "candidate"},
    "company": {"password": "company123", "role": "company"}
}
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

@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        if username in users and users[username]["password"] == password:
            session["user"] = username
            session["role"] = users[username]["role"]

            if users[username]["role"] == "candidate":
                return redirect(url_for("interview"))
            elif users[username]["role"] == "company":
                return redirect(url_for("company_dashboard"))
        
        return render_template("home.html", error="Invalid credentials")
    
    return render_template("home.html")

@app.route("/interview")
def interview():
    if "user" not in session or session["role"] != "candidate":
        return redirect(url_for("home"))
    return render_template("temp.html")

@app.route("/company_dashboard", methods=["GET", "POST"])
def company_dashboard():
    if "user" not in session or session["role"] != "company":
        return redirect(url_for("home"))

    if request.method == "POST":
        question = request.form["question"]
        interview_questions.append(question)
    
    return render_template("company_dashboard.html", interview_questions=interview_questions)


# 📌 **Route: Logout**
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))




# 🎤 Speech-to-Text (Azure STT)
@app.route("/speech_to_text", methods=["POST"])
def recognize_speech_from_mic():
    """Capture user speech and convert it to text using Azure STT."""
    try:
        speech_recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config)
        speech_config.speech_synthesis_voice_name = "en-IN-AashiNeural"

        print("Listening for speech...")
        result = speech_recognizer.recognize_once()

        if result.reason == speechsdk.ResultReason.RecognizedSpeech:
            return jsonify({"text": result.text})
        elif result.reason == speechsdk.ResultReason.NoMatch:
            return jsonify({"text": "No speech recognized."})
        else:
            return jsonify({"text": "Speech recognition failed."})
    except Exception as e:
        return jsonify({"error": str(e)})


# 📊 Evaluate Response
def evaluate_response(response):
    """Score the response (0-10) using Gemini AI."""
    global total_score, total_questions

    prompt = f"Evaluate this response on a scale of 0-10: '{response}'. Provide only an integer score."
    ai_response = model.generate_content(prompt)

    try:
        score_text = ai_response.text.strip()
        score = int(score_text)

        if 0 <= score <= 10:
            total_score += score
        else:
            raise ValueError("Invalid AI score")  # Handle invalid numbers

    except (ValueError, AttributeError):
        total_score += 5  # Default score if AI response is unclear

    total_questions += 1


# 🔊 Text-to-Speech (TTS)
@app.route("/text_to_speech", methods=["POST"])
def text_to_speech():
    """Convert text to speech using Azure Speech SDK and send audio to the client."""
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

    question_index = 0
    follow_up_remaining = 0
    total_score = 0
    total_questions = 0
    candidate_responses = []

    return get_question()  # Immediately send the first question


# 🤖 Get Next Question
@app.route("/get_question", methods=["GET"])
def get_question():
    global question_index, follow_up_remaining, total_score, total_questions, candidate_responses

    # Reset the interview if "start=true" is passed
    if request.args.get("start") == "true":
        question_index = 0
        follow_up_remaining = 0
        total_score = 0
        total_questions = 0
        candidate_responses = []
        return jsonify({"question": "Interview started! Here is your first question:", "type": "start"})

    # If interview is over
    if question_index >= len(interview_questions):
        final_score = round(total_score / total_questions, 2) if total_questions > 0 else 0
        return jsonify({"message": "Interview Completed!", "score": final_score})

    # If no follow-ups left, move to the next main question
    if follow_up_remaining == 0:
        current_question = interview_questions[question_index]
        with open("interview_log.txt", "a", encoding="utf-8") as log_file:
            log_file.write(f"Q: {current_question}\n")
        follow_up_remaining = int(1)
        # follow_up_remaining = random.randint(1, 3)
        return jsonify({"question": current_question, "type": "main"})

    # Generate a contextual follow-up question
    previous_answers = " ".join(candidate_responses[-1:]) or "No previous responses yet."
    prompt = (
        f"The candidate previously responded: '{previous_answers}'. "
        f"Ask a natural, engaging one-line follow-up question, avoiding robotic phrasing."
    )
    follow_up_question = model.generate_content(prompt).text.strip()
    with open("interview_log.txt", "a", encoding="utf-8") as log_file:
        log_file.write(f"Q: {follow_up_question}\n")
    follow_up_remaining -= 1  # Decrement follow-ups left

    if follow_up_remaining == 0:
        question_index += 1  # Move to the next main question

    return jsonify({"question": follow_up_question, "type": "follow-up"})

# 📩 Submit Candidate Response
@app.route("/submit_response", methods=["POST"])
def submit_response():
    data = request.get_json()
    user_response = data.get("response", "")
    candidate_responses.append(user_response)
    with open("interview_log.txt", "a", encoding="utf-8") as log_file:
        log_file.write(f"A: {user_response}\n\n")
    evaluate_response(user_response)
    return jsonify({"success": True})

@app.route("/get_feedback", methods=["GET"])
def get_feedback():
    """Pass the interview log to AI and get feedback."""
    try:
        # Read the Q&A from interview_log.txt
        with open("interview_log.txt", "r", encoding="utf-8") as log_file:
            interview_text = log_file.read()

        # ✅ Pass the full interview log to AI for evaluation
        prompt = f"""Analyze this interview session and provide constructive feedback, strengths, and areas of improvement. 

        Interview Transcript:
        {interview_text}

        Provide feedback in a structured manner:
        - **Overall Performance**
        - **Strengths**
        - **Areas for Improvement**
        - **Suggestions to Improve** 
        provide a score to him out of 10.
        """

        ai_response = model.generate_content(prompt)
        
        # Return AI feedback
        return jsonify({"feedback": ai_response.text.strip()})

    except Exception as e:
        return jsonify({"error": str(e)})


if __name__ == "__main__":
    app.run(debug=True)
