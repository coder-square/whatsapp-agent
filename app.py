from flask import Flask, request, jsonify
import anthropic
import requests
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

app = Flask(__name__)

# ─────────────────────────────────────────────
#  CONFIGURATION (set these in your .env file)
# ─────────────────────────────────────────────
WHATSAPP_TOKEN    = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID   = os.environ.get("PHONE_NUMBER_ID")
VERIFY_TOKEN      = os.environ.get("VERIFY_TOKEN", "my_secret_verify_token")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
EMAIL_SENDER      = os.environ.get("EMAIL_SENDER")      # your Gmail address
EMAIL_PASSWORD    = os.environ.get("EMAIL_PASSWORD")    # Gmail app password
EMAIL_RECEIVER    = os.environ.get("EMAIL_RECEIVER")    # where results are sent

import google.generativeai as genai

genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
model = genai.GenerativeModel("gemini-1.5-flash")

# ─────────────────────────────────────────────
#  YOUR FIXED RESEARCH QUESTIONS
#  Add / edit questions here before deploying
# ─────────────────────────────────────────────
RESEARCH_QUESTIONS = [
    "Before this conversation, how familiar were you with AI-powered tools being used for academic research interviews?",
    "In your professional experience, what factors most influence whether you agree to participate in a research study?",
    "How would you compare the experience of this WhatsApp interview with a traditional face-to-face or email-based interview?",
    "Do you feel this medium (WhatsApp AI agent) affected the honesty or depth of your responses in any way? Please explain.",
    "Would you participate in or recommend this method for future academic research? Why or why not?",
    # ← Add more specific questions here
]

RESEARCH_CONTEXT = """
You are a professional academic research interview agent conducting a study titled:
"The Effectiveness of WhatsApp Agents in Conducting Academic Interview Research."

Your respondents are working professionals. Your communication style must be:
- Warm, respectful, and encouraging (conversational)
- Credible and scholarly (academic)
- Concise — never overwhelming the respondent

You acknowledge answers before asking follow-ups, making respondents feel genuinely heard.
"""

# ─────────────────────────────────────────────
#  IN-MEMORY SESSION STORE
#  (For production, replace with Redis or a DB)
# ─────────────────────────────────────────────
sessions = {}


# ─────────────────────────────────────────────
#  CORE AI FUNCTIONS
# ─────────────────────────────────────────────

def generate_trigger_message():
    """Ask Claude to craft an irresistible recruitment message."""
    prompt = """
You are recruiting professionals to participate in a brief academic research interview via WhatsApp.

Write a compelling, irresistible WhatsApp message that:
1. Opens by making the recipient feel their professional expertise is uniquely valuable
2. Briefly states the research topic: the effectiveness of WhatsApp AI agents in conducting academic research
3. Builds curiosity — make them feel like they are part of something groundbreaking
4. Mentions it takes only 5–7 minutes
5. Creates a sense of meaningful academic contribution
6. Ends with a clear, frictionless call-to-action: reply YES to begin
7. Is warm, professional, under 160 words
8. Uses 1–2 emojis tastefully

Return only the message text, no labels or preamble.
"""
    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text.strip()


def generate_followup(question, answer, transcript_snippet):
    """Generate a smart, contextual follow-up question based on the respondent's answer."""
    prompt = f"""
{RESEARCH_CONTEXT}

The respondent just answered a fixed interview question:

QUESTION: {question}
ANSWER: {answer}

Recent conversation context:
{transcript_snippet}

Generate ONE follow-up question that:
1. Briefly and warmly acknowledges a specific point from their answer (1 sentence)
2. Probes deeper into that point in a way relevant to the research
3. Is open-ended and thought-provoking
4. Is no longer than 3 sentences total

Return only the follow-up text. No labels, no preamble.
"""
    response = model.generate_content(prompt)
return response.text.strip()


# ─────────────────────────────────────────────
#  WHATSAPP API
# ─────────────────────────────────────────────

def send_message(to, text):
    """Send a WhatsApp text message via Meta Cloud API."""
    url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text}
    }
    response = requests.post(url, headers=headers, json=payload)
    return response.json()


# ─────────────────────────────────────────────
#  EMAIL
# ─────────────────────────────────────────────

def email_transcript(respondent_number, transcript_lines):
    """Email the completed interview transcript to the researcher."""
    transcript_text = "\n".join(transcript_lines)
    msg = MIMEMultipart()
    msg["From"]    = EMAIL_SENDER
    msg["To"]      = EMAIL_RECEIVER
    msg["Subject"] = f"✅ Interview Completed — {respondent_number} [{datetime.now().strftime('%Y-%m-%d %H:%M')}]"

    body = f"""
A new research interview has been completed.

Respondent : {respondent_number}
Timestamp  : {datetime.now().strftime('%A, %d %B %Y at %H:%M:%S')}

{'═'*55}
FULL TRANSCRIPT
{'═'*55}

{transcript_text}

{'═'*55}
Sent automatically by your WhatsApp Research Agent
"""
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        print(f"[EMAIL] Transcript sent for {respondent_number}")
    except Exception as e:
        print(f"[EMAIL ERROR] {e}")


# ─────────────────────────────────────────────
#  INTERVIEW ENGINE
# ─────────────────────────────────────────────

def get_session(sender):
    if sender not in sessions:
        sessions[sender] = {
            "stage": "not_started",   # not_started | in_progress | completed
            "q_index": 0,
            "awaiting_followup_answer": False,
            "current_question": None,
            "transcript": []
        }
    return sessions[sender]


def process_message(sender, text):
    session = get_session(sender)
    stage   = session["stage"]

    # ── AWAITING CONSENT ──
    if stage == "not_started":
        if any(w in text.lower() for w in ["yes", "yeah", "yep", "ok", "okay", "sure", "start", "begin", "go"]):
            intro = (
                "Thank you! I'm truly glad you said yes. 🙏\n\n"
                "I'm an AI research agent conducting an academic study on:\n"
                "*'The Effectiveness of WhatsApp Agents in Conducting Academic Interview Research.'*\n\n"
                "Your responses are strictly confidential and used solely for academic purposes.\n\n"
                "The interview has *5 main questions* with brief follow-ups. "
                "There are no right or wrong answers — your honest professional perspective is what matters most.\n\n"
                "Let's begin! 📋"
            )
            send_message(sender, intro)

            first_q = RESEARCH_QUESTIONS[0]
            send_message(sender, f"*Question 1 of {len(RESEARCH_QUESTIONS)}:*\n\n{first_q}")
            session["stage"]                    = "in_progress"
            session["q_index"]                  = 0
            session["awaiting_followup_answer"] = False
            session["current_question"]         = first_q
            session["transcript"].append(f"[Interview started: {datetime.now().strftime('%Y-%m-%d %H:%M')}]\n")
            session["transcript"].append(f"Q1: {first_q}")
        else:
            send_message(sender, (
                "Hello! 👋 You've been invited to participate in a short academic research interview.\n\n"
                "Reply *YES* to begin — it takes just 5–7 minutes and your insights truly matter. 😊"
            ))
        return

    # ── INTERVIEW IN PROGRESS ──
    if stage == "in_progress":
        q_index   = session["q_index"]
        awaiting  = session["awaiting_followup_answer"]
        current_q = session["current_question"]

        # Record the answer
        label = "Follow-up answer" if awaiting else f"A{q_index + 1}"
        session["transcript"].append(f"{label}: {text}")

        if not awaiting:
            # Generate AI follow-up for this fixed question
            snippet  = "\n".join(session["transcript"][-5:])
            followup = generate_followup(current_q, text, snippet)
            send_message(sender, followup)
            session["transcript"].append(f"Follow-up: {followup}")
            session["awaiting_followup_answer"] = True

        else:
            # Move to next fixed question
            next_index = q_index + 1
            session["awaiting_followup_answer"] = False

            if next_index < len(RESEARCH_QUESTIONS):
                next_q = RESEARCH_QUESTIONS[next_index]
                q_num  = next_index + 1
                session["transcript"].append(f"\nQ{q_num}: {next_q}")
                send_message(sender, f"*Question {q_num} of {len(RESEARCH_QUESTIONS)}:*\n\n{next_q}")
                session["q_index"]          = next_index
                session["current_question"] = next_q

            else:
                # ── INTERVIEW COMPLETE — WITH CALL TO ACTION ──
                closing = (
                    "And that brings us to the end of our interview! 🎉\n\n"
                    "Thank you sincerely for your time and your thoughtful, honest responses. "
                    "Professionals like you are what make academic research meaningful.\n\n"
                    "Your insights will directly contribute to understanding how AI can ethically "
                    "and effectively support academic research — a growing field with real implications.\n\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "🎁 *Two exclusive offers for you:*\n\n"
                    "📄 *1. Get the Findings* — Once our study is published, we'll send you the full "
                    "research findings directly here on WhatsApp. You'll be among the first to see how "
                    "this technology is shaping the future of academic research.\n\n"
                    "🚀 *2. Early Access — 80% Off* — We are onboarding our *first 50 users* to this "
                    "WhatsApp Interview Agent platform. As a participant, you qualify for *80% off* the "
                    "standard price — exclusively reserved for people like you who helped shape it.\n\n"
                    "👉 Reply *JOIN* to secure your early access spot\n"
                    "👉 Reply *RESULTS* to receive the published findings\n"
                    "👉 Reply *BOTH* for both offers\n\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "Spots are limited and filling fast. Thank you for being part of something truly groundbreaking! 😊🙏"
                )
                send_message(sender, closing)
                session["transcript"].append(f"\n[Interview completed: {datetime.now().strftime('%Y-%m-%d %H:%M')}]")
                session["stage"] = "completed"
                email_transcript(sender, session["transcript"])

    # ── POST-INTERVIEW STAGE ──
    elif stage == "completed":
        lower = text.lower()

        if "both" in lower:
            send_message(sender, (
                "🙌 Wonderful! You're locked in for *both*:\n\n"
                "✅ Research findings — we'll send them once published\n"
                "✅ Early access — you're on our *First 50* list at *80% off*\n\n"
                "We'll be in touch soon. Thank you for being a pioneer! 🚀"
            ))
        elif "join" in lower:
            send_message(sender, (
                "🚀 You're in! We've added you to our *First 50 Early Access* list.\n\n"
                "You'll receive your *80% discount code* as soon as the platform launches. "
                "Keep an eye on this chat — exciting things are coming! 🎉"
            ))
        elif "results" in lower:
            send_message(sender, (
                "📬 Noted! Once the research is published, we'll send the full findings directly here.\n\n"
                "Thank you again for your valuable contribution to this study! 🙏"
            ))
        else:
            send_message(sender, (
                "You've already completed the interview — thank you! 🙏\n\n"
                "Reply *JOIN* to secure early access at 80% off\n"
                "Reply *RESULTS* to receive the published findings\n"
                "Reply *BOTH* for both offers 🎁"
            ))


# ─────────────────────────────────────────────
#  FLASK ROUTES
# ─────────────────────────────────────────────

@app.route("/webhook", methods=["GET"])
def verify():
    """Meta webhook verification."""
    mode      = request.args.get("hub.mode")
    token     = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("[WEBHOOK] Verified successfully")
        return challenge, 200
    return "Forbidden", 403


@app.route("/webhook", methods=["POST"])
def webhook():
    """Receive incoming WhatsApp messages."""
    data = request.json
    try:
        messages = data["entry"][0]["changes"][0]["value"].get("messages", [])
        for message in messages:
            sender = message["from"]
            if message["type"] == "text":
                text = message["text"]["body"]
                print(f"[MESSAGE] From {sender}: {text}")
                process_message(sender, text)
    except Exception as e:
        print(f"[WEBHOOK ERROR] {e}")
    return jsonify({"status": "ok"}), 200


@app.route("/send-trigger", methods=["POST"])
def send_trigger():
    """
    Send the AI-generated recruitment trigger to one or more respondents.

    POST body (JSON):
    {
        "numbers": ["2348012345678", "2348087654321"]
    }

    Phone numbers must include country code, no '+' or spaces.
    """
    body    = request.json or {}
    numbers = body.get("numbers", [])

    if not numbers:
        return jsonify({"error": "Provide a 'numbers' list in the request body"}), 400

    trigger = generate_trigger_message()
    results = []

    for number in numbers:
        resp = send_message(number, trigger)
        # Initialize session so the agent is ready when they reply
        sessions[number] = {
            "stage": "not_started",
            "q_index": 0,
            "awaiting_followup_answer": False,
            "current_question": None,
            "transcript": []
        }
        results.append({"number": number, "status": "sent", "whatsapp_response": resp})
        print(f"[TRIGGER] Sent to {number}")

    return jsonify({
        "trigger_message_used": trigger,
        "results": results
    })


@app.route("/sessions", methods=["GET"])
def view_sessions():
    """Quick overview of all active sessions (for debugging)."""
    summary = {
        num: {"stage": s["stage"], "q_index": s["q_index"]}
        for num, s in sessions.items()
    }
    return jsonify(summary)


@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "running",
        "agent": "WhatsApp Academic Research Interview Agent",
        "endpoints": {
            "POST /send-trigger": "Send recruitment message to respondents",
            "GET|POST /webhook":  "WhatsApp webhook",
            "GET /sessions":      "View active sessions"
        }
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
