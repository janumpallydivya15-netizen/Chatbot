from datetime import datetime
from email.utils import formataddr
from email.message import EmailMessage
import os
from pathlib import Path
import re
import ssl
import smtplib
import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from markupsafe import Markup

app = Flask(__name__)
app.secret_key = "careconnect-ai-demo-secret"

@app.template_filter('markdown')
def markdown_filter(text):
    if not text:
        return ""
    import html
    escaped_text = html.escape(str(text))
    # Replace bold text
    escaped_text = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', escaped_text)
    # Replace italic text
    escaped_text = re.sub(r'\*(.*?)\*', r'<em>\1</em>', escaped_text)
    # Replace new lines with breaks
    escaped_text = escaped_text.replace('\n', '<br>')
    return Markup(escaped_text)


def load_env_file():
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        cleaned_value = value.strip().strip('"').strip("'")
        os.environ[key.strip()] = cleaned_value


load_env_file()

SMTP_HOST = os.getenv("CARECONNECT_SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("CARECONNECT_SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("CARECONNECT_SMTP_USERNAME", "")
SMTP_PASSWORD = "".join(os.getenv("CARECONNECT_SMTP_PASSWORD", "").split())
SMTP_USE_TLS = os.getenv("CARECONNECT_SMTP_USE_TLS", "true").lower() == "true"
ALERT_EMAIL_FROM_NAME = os.getenv("CARECONNECT_ALERT_FROM_NAME", "CareConnect")
ALERT_EMAIL_FROM = os.getenv(
    "CARECONNECT_ALERT_EMAIL_FROM", ""
)
ALERT_EMAIL_TO = os.getenv(
    "CARECONNECT_ALERT_EMAIL_TO", "janumpallydivya51@gmail.com"
)
AWS_REGION = os.getenv("CARECONNECT_AWS_REGION", "us-east-1")
RESPONSE_PROVIDER = os.getenv("CARECONNECT_RESPONSE_PROVIDER", "lex").strip().lower()
BEDROCK_REGION = os.getenv("CARECONNECT_BEDROCK_REGION", AWS_REGION)
BEDROCK_MODEL_ID = os.getenv("CARECONNECT_BEDROCK_MODEL_ID", "").strip()
BEDROCK_MAX_TOKENS = int(os.getenv("CARECONNECT_BEDROCK_MAX_TOKENS", "220"))
BEDROCK_TEMPERATURE = float(os.getenv("CARECONNECT_BEDROCK_TEMPERATURE", "0.3"))

lex_client = boto3.client(
    "lexv2-runtime",
    region_name=AWS_REGION,
    config=Config(connect_timeout=2, read_timeout=4, retries={"max_attempts": 1}),
)
bedrock_client = boto3.client(
    "bedrock-runtime",
    region_name=BEDROCK_REGION,
    config=Config(connect_timeout=2, read_timeout=8, retries={"max_attempts": 1}),
)

BOT_ID = "Z99GXHLUC8"
BOT_ALIAS_ID = "TSTALIASID"
LOCALE_ID = "en_US"

SEVERE_SYMPTOM_PATTERNS = {
    "chest pain": ["chest pain", "pain in chest"],
    "difficulty breathing": [
        "difficulty breathing",
        "shortness of breath",
        "short of breath",
        "breathing issue",
        "breathing issues",
        "breathing problem",
        "breathing problems",
        "breathing trouble",
        "trouble breathing",
        "hard to breathe",
        "breathless",
        "cannot breathe",
        "cant breathe",
    ],
    "severe bleeding": [
        "severe bleeding",
        "heavy bleeding",
        "bleeding heavily",
        "bleeding a lot",
        "bleeding from mouth",
        "mouth bleeding",
        "bleeding in mouth",
        "blood from mouth",
        "blood coming from mouth",
        "bleeding gums",
        "spitting blood",
        "coughing blood",
    ],
    "unconscious": ["unconscious", "passed out", "not conscious"],
    "seizure": ["seizure", "convulsion"],
    "stroke": ["stroke", "face drooping", "slurred speech"],
    "heart attack": ["heart attack", "heartattack"],
    "cancer": ["cancer", "tumor", "tumour", "malignant"],
    "high fever": ["high fever", "very high fever"],
    "blood in vomit": ["blood in vomit", "vomiting blood"],
    "bloody stool": ["bloody stool", "blood in stool", "black stool", "black stools"],
    "fainting": ["fainting", "fainted"],
    "blue lips": ["blue lips", "bluish lips"],
    "swallowing difficulty": [
        "difficulty swallowing",
        "trouble swallowing",
        "swallowing issue",
        "swallowing issues",
        "cannot swallow",
        "cant swallow",
        "pain while swallowing",
    ],
    "road accident injury": [
        "road accident",
        "car accident",
        "bike accident",
        "vehicle accident",
        "met with an accident",
        "met with accident",
        "had an accident",
        "got into an accident",
        "i met with an accident",
        "met with accident",
        "after accident",
        "after a fall",
        "fell down",
        "head injury",
        "neck pain after accident",
        "neck pain after fall",
    ],
}

EMERGENCY_SEVERITY_LABELS = {
    "chest pain",
    "difficulty breathing",
    "severe bleeding",
    "unconscious",
    "seizure",
    "stroke",
    "heart attack",
    "blood in vomit",
    "bloody stool",
    "fainting",
    "blue lips",
    "swallowing difficulty",
    "road accident injury",
}

SEVERE_ALERT_MESSAGE = (
    "Severe symptoms detected. Please seek urgent medical attention immediately. "
    "CareConnect is also sending an alert to the configured email address."
)
BEDROCK_SYSTEM_PROMPT = (
    "You are CareConnect AI, a helpful medical and general assistant. "
    "Respond accurately to whatever the user asks according to their question. "
    "Give clear, practical, and helpful answers. "
    "If the question is about symptoms, offer guidance but do not diagnose with certainty. "
    "If symptoms sound urgent, advise immediate medical care. "
    "Be friendly and conversational."
)
LOW_SIGNAL_REPLY_PATTERNS = [
    "i did not understand",
    "i didn't understand",
    "can you repeat",
    "please repeat",
    "could you rephrase",
    "try again",
    "rest, stay hydrated",
    "tell me more",
]
ACCIDENT_KEYWORDS = [
    "accident",
    "road accident",
    "car accident",
    "bike accident",
    "vehicle accident",
    "crash",
    "collision",
    "fell down",
    "fall",
    "injury",
    "hit my head",
    "head injury",
]
TRAUMA_PAIN_KEYWORDS = [
    "neck pain",
    "back pain",
    "head pain",
    "headache",
    "shoulder pain",
    "leg pain",
    "arm pain",
    "bleeding",
    "unconscious",
    "dizzy",
    "dizziness",
]
SYMPTOM_GUIDANCE_RULES = [
    {
        "label": "fever",
        "patterns": ["fever", "temperature", "chills"],
        "guidance": "Rest, drink plenty of fluids, and keep checking your temperature. Please see a doctor if the fever stays high, lasts more than 2 days, or comes with confusion or dehydration.",
    },
    {
        "label": "cough",
        "patterns": ["cough", "coughing"],
        "guidance": "Warm fluids, rest, and staying hydrated may help with cough. Please seek medical advice if you also have chest pain, breathing trouble, or a fever that is not settling.",
    },
    {
        "label": "cold",
        "patterns": ["cold", "runny nose", "blocked nose", "nasal congestion", "stuffy nose", "sneezing"],
        "guidance": "Cold symptoms often improve with rest, fluids, and sleep. If symptoms keep getting worse or do not improve over the next few days, please consult a doctor.",
    },
    {
        "label": "headache",
        "patterns": ["headache", "migraine", "head pain"],
        "guidance": "Rest, water, and avoiding bright screens may help with headache. Please see a doctor if the headache is severe, repeated, or comes with vomiting, weakness, or blurred vision.",
    },
    {
        "label": "stomach pain",
        "patterns": ["stomach pain", "abdominal pain", "belly pain", "abdomen pain", "cramps", "cramping"],
        "guidance": "Try light foods and fluids if you can tolerate them. Please see a doctor if the pain becomes severe, localizes to one side, or comes with vomiting, fever, or swelling.",
    },
    {
        "label": "vomiting",
        "patterns": ["vomiting", "vomit", "throwing up", "nausea"],
        "guidance": "Sip water or oral fluids slowly to avoid dehydration. Please seek medical care if you cannot keep fluids down, feel very weak, or notice blood in vomit.",
    },
    {
        "label": "diarrhea",
        "patterns": ["diarrhea", "loose motion", "loose motions", "loose stool", "watery stool"],
        "guidance": "Drink plenty of fluids or oral rehydration solution and avoid oily foods for now. Please consult a doctor if it continues, becomes severe, or you feel dizzy or dehydrated.",
    },
    {
        "label": "sore throat",
        "patterns": ["sore throat", "throat pain", "pain while swallowing"],
        "guidance": "Warm fluids and rest may help a sore throat. Please see a doctor if swallowing becomes difficult, fever is high, or symptoms keep worsening.",
    },
    {
        "label": "body pain",
        "patterns": ["body pain", "body ache", "body aches", "muscle pain", "muscle ache", "joint pain"],
        "guidance": "Rest and fluids may help with body pain. Please seek medical advice if the pain is severe, keeps returning, or comes with fever, rash, or weakness.",
    },
    {
        "label": "back pain",
        "patterns": ["back pain", "lower back pain", "upper back pain"],
        "guidance": "Gentle rest and avoiding strain may help with back pain. Please see a doctor if the pain is severe, spreads to the legs, or comes with numbness or trouble passing urine.",
    },
    {
        "label": "eye irritation",
        "patterns": ["eye irritation", "eyes irritation", "eye irritating", "eyes irritating", "itchy eyes", "eye itching", "eye pain", "red eyes", "burning eyes", "watery eyes"],
        "keyword_groups": [["eye", "eyes"], ["irritat", "itch", "red", "burn", "water", "pain", "swelling", "discharge"]],
        "guidance": "Try not to rub your eyes and avoid dust, smoke, or anything that seems to trigger the irritation. Please see a doctor if the redness or irritation becomes severe, affects vision, or comes with swelling, discharge, or significant pain.",
    },
    {
        "label": "skin rash",
        "patterns": ["rash", "skin rash", "itching", "itchy skin", "hives"],
        "guidance": "Avoid scratching and notice whether the rash is spreading or linked to a new food, medicine, or skin product. Please seek medical care quickly if you also have swelling, breathing trouble, or fever.",
    },
    {
        "label": "mouth bleeding",
        "patterns": ["bleeding from mouth", "mouth bleeding", "bleeding in mouth", "blood from mouth", "blood coming from mouth", "bleeding gums", "spitting blood", "coughing blood"],
        "guidance": "Bleeding from the mouth is not something to ignore. Please seek urgent medical care, especially if the bleeding is continuing, you feel weak or dizzy, or you also have chest pain, breathing trouble, or vomiting blood.",
    },
    {
        "label": "burning urination",
        "patterns": ["burning urination", "pain while urinating", "painful urination", "burning while peeing", "uti", "urine infection"],
        "guidance": "Drink fluids and monitor for worsening discomfort or fever. Please see a doctor if the burning continues, urine frequency increases, or you develop lower abdominal pain or fever.",
    },
    {
        "label": "constipation",
        "patterns": ["constipation", "hard stool", "hard stools", "not passing stool"],
        "guidance": "Fluids, fiber-rich foods, and gentle movement may help with constipation. Please see a doctor if you also have severe pain, vomiting, or blood in the stool.",
    },
    {
        "label": "fatigue",
        "patterns": ["fatigue", "tired", "weakness", "weak", "exhausted"],
        "guidance": "Rest, fluids, and regular meals may help if you are feeling tired or weak. Please see a doctor if the weakness is sudden, severe, or keeps returning without a clear reason.",
    },
]


def mask_email(email):
    if not email or "@" not in email:
        return "not configured"

    local_part, domain = email.split("@", 1)
    if len(local_part) <= 2:
        masked_local = local_part[0] + "*"
    else:
        masked_local = local_part[0] + ("*" * (len(local_part) - 2)) + local_part[-1]

    return f"{masked_local}@{domain}"


def normalize_text(text):
    normalized = text.lower().replace("-", " ")
    normalized = re.sub(r"[^a-z0-9\s]", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def normalize_match_word(word):
    for suffix in ("ings", "ing", "ed", "es", "s"):
        if len(word) - len(suffix) >= 3 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def normalize_match_text(text):
    normalized = normalize_text(text)
    return " ".join(normalize_match_word(word) for word in normalized.split())


def explain_email_error(exc):
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return (
            "Gmail rejected the email login. Update CARECONNECT_SMTP_USERNAME and "
            "use a valid 16-character Gmail App Password for CARECONNECT_SMTP_PASSWORD."
        )

    return str(exc) or exc.__class__.__name__


def extract_bedrock_text(response):
    content_blocks = (
        response.get("output", {})
        .get("message", {})
        .get("content", [])
    )
    text_parts = [block.get("text", "").strip() for block in content_blocks if block.get("text")]
    return " ".join(part for part in text_parts if part).strip()


def extract_lex_slot_values(slots):
    extracted = {}
    if not isinstance(slots, dict):
        return extracted

    for slot_name, slot_value in slots.items():
        if not isinstance(slot_value, dict):
            continue

        value = slot_value.get("value", {})
        interpreted_value = value.get("interpretedValue") or value.get("originalValue")
        if interpreted_value:
            extracted[slot_name] = interpreted_value

    return extracted


def get_recent_chat_context(limit=4):
    history = session.get("chat_history", [])
    if not history:
        return ""

    recent_items = history[-limit:]
    lines = []
    for item in recent_items:
        user_text = (item.get("user_message") or "").strip()
        bot_text = (item.get("bot_reply") or "").strip()
        if user_text:
            lines.append(f"Patient: {user_text}")
        if bot_text:
            lines.append(f"CareConnect: {bot_text}")

    return "\n".join(lines)


def get_recent_user_messages(limit=3):
    history = session.get("chat_history", [])
    if not history:
        return []

    messages = []
    for item in history[-limit:]:
        user_text = (item.get("user_message") or "").strip()
        if user_text:
            messages.append(user_text)

    return messages


def is_follow_up_symptom_message(user_message):
    normalized = normalize_text(user_message)
    follow_up_starters = (
        "it ",
        "it was",
        "it is",
        "its ",
        "since ",
        "from ",
        "for ",
        "also ",
        "and ",
        "now ",
        "still ",
    )
    return normalized.startswith(follow_up_starters)


def build_effective_user_message(user_message):
    recent_user_messages = get_recent_user_messages()
    if not recent_user_messages or not is_follow_up_symptom_message(user_message):
        return user_message

    previous_context = recent_user_messages[-1]
    return f"Previous symptom context: {previous_context}. Current update: {user_message}"


def get_bedrock_response(user_message, lex_result=None, severity_matches=None):
    if not BEDROCK_MODEL_ID:
        return "DEBUG: BEDROCK_MODEL_ID is empty!"

    history = session.get("chat_history", [])
    messages = []
    
    for item in history[-5:]:
        if item.get("user_message"):
            messages.append({"role": "user", "content": [{"text": item["user_message"]}]})
        if item.get("bot_reply"):
            messages.append({"role": "assistant", "content": [{"text": item["bot_reply"]}]})
            
    current_text = user_message
    if severity_matches:
         current_text += f"\n[System Note: Severity flags detected: {', '.join(severity_matches)}]"
         
    messages.append({"role": "user", "content": [{"text": current_text}]})

    try:
        response = bedrock_client.converse(
            modelId=BEDROCK_MODEL_ID,
            system=[{"text": BEDROCK_SYSTEM_PROMPT}],
            messages=messages,
            inferenceConfig={
                "maxTokens": BEDROCK_MAX_TOKENS,
                "temperature": BEDROCK_TEMPERATURE,
            },
        )
    except Exception as e:
        return f"DEBUG: Bedrock Converse Exception: {str(e)}"
        
    bedrock_reply = extract_bedrock_text(response)
    if not bedrock_reply:
         return f"DEBUG: extract_bedrock_text returned empty. Response: {str(response)[:200]}"
    return bedrock_reply


def extract_detected_symptoms(user_message):
    message = normalize_text(user_message)
    normalized_message = normalize_match_text(user_message)
    detected = []

    for rule in SYMPTOM_GUIDANCE_RULES:
        matched_pattern = any(contains_phrase(message, pattern) for pattern in rule["patterns"])
        keyword_groups = rule.get("keyword_groups", [])
        matched_groups = bool(keyword_groups) and all(
            any(group_term in normalized_message for group_term in keyword_group)
            for keyword_group in keyword_groups
        )

        if matched_pattern or matched_groups:
            detected.append(rule)

    return detected


def format_label_list(labels):
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    return f"{', '.join(labels[:-1])}, and {labels[-1]}"


def is_low_signal_reply(reply):
    if not reply:
        return True

    normalized_reply = normalize_text(reply)
    if len(normalized_reply) < 20:
        return True

    return any(pattern in normalized_reply for pattern in LOW_SIGNAL_REPLY_PATTERNS)


def build_detected_symptom_response(user_message, detected_symptoms):
    labels = [rule["label"] for rule in detected_symptoms[:4]]
    label_set = set(labels)
    message = normalize_text(user_message)

    if {"fever", "cough", "cold"} & label_set and len(label_set & {"fever", "cough", "cold"}) >= 2:
        return (
            f"You seem to be describing a mix of {format_label_list(labels)} symptoms. "
            "That pattern can happen with a viral illness or throat/airway infection. Rest, warm fluids, and monitoring fever can help for now, "
            "but please see a doctor if breathing becomes difficult, fever stays high, or symptoms are getting worse instead of improving."
        )

    if "vomiting" in label_set and any(term in message for term in ["after eating", "food", "biryani", "outside food"]):
        return (
            f"I understand that you are dealing with {format_label_list(labels)} after eating. "
            "This may happen with food irritation or food poisoning. Sip fluids slowly, avoid heavy food for now, and seek medical care if vomiting keeps happening, "
            "you cannot keep fluids down, or you feel weak, dizzy, or feverish."
        )

    if "eye irritation" in label_set:
        return (
            f"I understand that you are dealing with {format_label_list(labels)}. "
            "Try not to rub your eyes and avoid dust, smoke, or anything that seems to worsen the irritation. "
            "Please see a doctor if vision is affected, the redness becomes severe, or there is swelling, discharge, or significant pain."
        )

    if "stomach pain" in label_set and "vomiting" in label_set:
        return (
            f"You mentioned both {format_label_list(labels)}. "
            "That combination can happen with stomach irritation or infection. Take small sips of fluids and avoid heavy food for now, "
            "and please seek medical care if the pain becomes severe, vomiting continues, or you notice fever, dehydration, or blood."
        )

    if "skin rash" in label_set:
        return (
            f"I understand that you are dealing with {format_label_list(labels)}. "
            "Try to avoid scratching and notice whether the rash is spreading or linked to a new food, medicine, or skin product. "
            "Please get medical help quickly if you also have swelling, breathing trouble, or fever."
        )

    if "burning urination" in label_set:
        return (
            f"I understand that you are dealing with {format_label_list(labels)}. "
            "Drink fluids and keep track of whether the burning is improving or becoming more frequent. "
            "Please see a doctor if this continues, or if you develop fever, lower abdominal pain, or worsening discomfort."
        )

    guidance_lines = [rule["guidance"] for rule in detected_symptoms[:2]]
    combined_guidance = " ".join(guidance_lines)
    return (
        f"I understand that you are dealing with {format_label_list(labels)}. "
        f"{combined_guidance}"
    )


def get_lex_result(user_message):
    session_id = str(uuid.uuid4())
    response = lex_client.recognize_text(
        botId=BOT_ID,
        botAliasId=BOT_ALIAS_ID,
        localeId=LOCALE_ID,
        sessionId=session_id,
        text=user_message,
    )

    messages = response.get("messages", [])
    session_state = response.get("sessionState", {})
    intent = session_state.get("intent", {}) if isinstance(session_state, dict) else {}

    return {
        "reply": messages[0].get("content") if messages else None,
        "intent_name": intent.get("name", ""),
        "slot_values": extract_lex_slot_values(intent.get("slots", {})),
        "raw_response": response,
    }


def build_chat_response(user_message, severity_matches):
    try:
        bedrock_reply = get_bedrock_response(user_message, None, severity_matches)
        if bedrock_reply:
            return bedrock_reply
        return "Bedrock returned an empty response."
    except Exception as e:
        return f"Bedrock API Error: {str(e)}"


def contains_phrase(message, phrase):
    normalized_message = normalize_match_text(message)
    normalized_phrase = normalize_match_text(phrase)
    return f" {normalized_phrase} " in f" {normalized_message} "


def build_severe_response(severity_matches):
    severe_list = format_label_list(severity_matches)

    if any(match in EMERGENCY_SEVERITY_LABELS for match in severity_matches):
        return (
            f"The combination of {severe_list} sounds severe and may need urgent medical attention. "
            "Please contact emergency services or go to the nearest hospital immediately."
        )

    if "swallowing difficulty" in severity_matches and "difficulty breathing" in severity_matches:
        return (
            "Having both swallowing difficulty and breathing trouble can be urgent. "
            "Please seek emergency medical care immediately or go to the nearest hospital right away."
        )

    return (
        f"The symptoms you mentioned, including {severe_list}, sound serious and need urgent medical review. "
        "Please contact a doctor or go to the nearest hospital as soon as possible."
    )


def build_contextual_general_response(user_message):
    message = normalize_text(user_message)

    stomach_patterns = [
        "vomit", "nausea", "stomach pain", "abdominal pain", "diarrhea", "loose stool"
    ]
    food_patterns = [
        "after eating", "after food", "food", "biryani", "rice", "chicken", "outside food"
    ]
    pain_patterns = [
        "pain", "ache", "cramp", "burning"
    ]
    breathing_patterns = [
        "cough", "cold", "breath", "breathing", "throat", "nose"
    ]
    skin_patterns = [
        "rash", "itch", "skin", "swelling", "hives"
    ]
    weakness_patterns = [
        "weak", "weakness", "fatigue", "tired", "dizzy", "dizziness"
    ]

    if any(pattern in message for pattern in stomach_patterns) and any(
        pattern in message for pattern in food_patterns
    ):
        return (
            f"You mentioned symptoms after eating: {user_message}. "
            "This can happen with food irritation or food poisoning. Sip fluids slowly, avoid heavy or oily food for now, "
            "and seek medical care if vomiting keeps happening, you cannot keep fluids down, or you feel weak, dizzy, or feverish."
        )

    if any(pattern in message for pattern in stomach_patterns):
        return (
            f"I understand you are dealing with stomach-related symptoms: {user_message}. "
            "Try taking small sips of fluids and avoid heavy food for now. Please see a doctor if the symptoms keep getting worse, "
            "last more than expected, or come with dehydration, blood, or severe pain."
        )

    if any(pattern in message for pattern in pain_patterns):
        return (
            f"I hear that you are having pain or discomfort: {user_message}. "
            "Please rest the affected area if possible and monitor whether the pain is getting stronger, spreading, or limiting movement. "
            "Seek medical care if the pain is severe, follows an injury, or comes with swelling, fever, weakness, or dizziness."
        )

    if any(pattern in message for pattern in breathing_patterns):
        return (
            f"I understand that you are having breathing or throat-related symptoms: {user_message}. "
            "Rest, fluids, and watching for worsening symptoms can help in mild cases. Please seek urgent care if you develop chest pain, "
            "shortness of breath, wheezing, or trouble swallowing."
        )

    if any(pattern in message for pattern in skin_patterns):
        return (
            f"You mentioned skin-related symptoms: {user_message}. "
            "Please avoid scratching or using new skin products for now, and watch whether the rash or swelling is spreading. "
            "Get medical help quickly if you also have breathing trouble, fever, or swelling of the lips or face."
        )

    if any(pattern in message for pattern in weakness_patterns):
        return (
            f"I understand that you are feeling weak or unwell: {user_message}. "
            "Please rest, drink fluids if you can, and keep track of whether this is improving or getting worse. "
            "Please seek medical care if the weakness is sudden, severe, or comes with chest pain, fainting, fever, or breathing trouble."
        )

    return (
        f"Thank you for sharing your symptoms: {user_message}. "
        "I may need a little more detail to guide you better, such as when it started, whether it is getting worse, and whether you have fever, pain, breathing trouble, vomiting, or dizziness. "
        "If the symptom becomes severe or feels unusual for you, please contact a doctor."
    )


def build_fallback_response(user_message):
    severity_matches = get_severity_matches(user_message)
    if severity_matches:
        return build_severe_response(severity_matches)

    detected_symptoms = extract_detected_symptoms(user_message)
    if detected_symptoms:
        return build_detected_symptom_response(user_message, detected_symptoms)

    return build_contextual_general_response(user_message)


def get_severity_matches(user_message):
    message = normalize_text(user_message)
    matches = []

    for label, patterns in SEVERE_SYMPTOM_PATTERNS.items():
        if any(contains_phrase(message, pattern) for pattern in patterns):
            matches.append(label)

    has_accident_context = any(contains_phrase(message, keyword) for keyword in ACCIDENT_KEYWORDS)
    has_trauma_symptom = any(contains_phrase(message, keyword) for keyword in TRAUMA_PAIN_KEYWORDS)
    if has_accident_context and has_trauma_symptom and "road accident injury" not in matches:
        matches.append("road accident injury")
    if has_accident_context and "road accident injury" not in matches:
        matches.append("road accident injury")

    return matches


def send_severity_alert_email(user_message, severity_matches, patient_name, patient_email):
    sender_email = ALERT_EMAIL_FROM or SMTP_USERNAME

    if not (SMTP_HOST and ALERT_EMAIL_TO and sender_email):
        return {
            "sent": False,
            "error": "Alert email settings are incomplete.",
        }

    if not (SMTP_USERNAME and SMTP_PASSWORD):
        return {
            "sent": False,
            "error": "SMTP username or app password is missing.",
        }

    email_message = EmailMessage()
    email_message["Subject"] = "CareConnect AI Severity Alert"
    authenticated_sender = SMTP_USERNAME or sender_email
    email_message["From"] = formataddr((ALERT_EMAIL_FROM_NAME, authenticated_sender))
    email_message["To"] = ALERT_EMAIL_TO
    if sender_email and sender_email != authenticated_sender:
        email_message["Reply-To"] = formataddr((ALERT_EMAIL_FROM_NAME, sender_email))
    email_message.set_content(
        "\n".join(
            [
                "A severe symptom alert was triggered in CareConnect AI.",
                "",
                f"Time: {datetime.now().strftime('%d %b %Y, %I:%M %p')}",
                f"Patient: {patient_name}",
                f"Patient Email: {patient_email}",
                f"Matched Severity Keywords: {', '.join(severity_matches)}",
                "",
                "Submitted Symptoms:",
                user_message,
            ]
        )
    )

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as smtp:
            smtp.ehlo()
            if SMTP_USE_TLS:
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
            if SMTP_USERNAME and SMTP_PASSWORD:
                smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
            smtp.send_message(email_message)
        return {
            "sent": True,
            "error": "",
        }
    except Exception as exc:
        return {
            "sent": False,
            "error": explain_email_error(exc),
        }


def trigger_severity_alert(user_message, severity_matches=None):
    severity_matches = severity_matches if severity_matches is not None else get_severity_matches(user_message)
    if not severity_matches:
        return {
            "sent": False,
            "error": "",
            "target_email": ALERT_EMAIL_TO,
        }

    current_user = session.get("user", {})
    patient_name = current_user.get("name", "Guest User")
    patient_email = current_user.get("email", "Not provided")

    # Send immediately so failures can be surfaced in the chat response.
    email_status = send_severity_alert_email(
        user_message,
        severity_matches,
        patient_name,
        patient_email,
    )
    email_status["target_email"] = ALERT_EMAIL_TO
    return email_status


def save_chat_entry(user_message, bot_reply):
    history = session.get("chat_history", [])
    history.append(
        {
            "user_message": user_message,
            "bot_reply": bot_reply,
            "created_at": datetime.now().strftime("%d %b %Y, %I:%M %p"),
        }
    )
    session["chat_history"] = history[-20:]
    session.modified = True


def display_name_from_email(email):
    return email.split("@")[0].replace(".", " ").replace("_", " ").title()


@app.route("/")
def home():
    return render_template("home.html")


@app.route("/chat-page")
def chat_page():
    return render_template("chat.html", chat_history=session.get("chat_history", []))


@app.route("/history")
def history_page():
    history = list(reversed(session.get("chat_history", [])))
    return render_template("history.html", history=history)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()

        if not email or not password:
            flash("Please enter both email and password.", "error")
        else:
            session["user"] = {
                "name": display_name_from_email(email),
                "email": email,
            }
            flash("Signed in successfully. Your care assistant is ready.", "success")
            return redirect(url_for("chat_page"))

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()

        if not name or not email or not password:
            flash("Please complete all registration fields.", "error")
        else:
            session["user"] = {"name": name, "email": email}
            flash("Account created successfully. Welcome to CareConnect AI.", "success")
            return redirect(url_for("chat_page"))

    return render_template("register.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.pop("user", None)
    session.pop("chat_history", None)
    flash("You have been signed out.", "success")
    return redirect(url_for("home"))


@app.route("/clear-history", methods=["POST"])
def clear_history():
    session["chat_history"] = []
    flash("Your conversation history has been cleared.", "success")
    return redirect(request.referrer or url_for("history_page"))


@app.route("/chat", methods=["POST"])
def chat():
    try:
        data = request.get_json(silent=True) or {}
        user_message = (data.get("message") or "").strip()

        if not user_message:
            return jsonify({"reply": "Please enter your symptoms first."}), 400

        effective_user_message = build_effective_user_message(user_message)
        severity_matches = get_severity_matches(effective_user_message)
        bot_reply = build_chat_response(effective_user_message, severity_matches)

        email_status = {
            "sent": False,
            "error": "",
            "target_email": ALERT_EMAIL_TO,
        }
        if severity_matches:
            try:
                email_status = trigger_severity_alert(effective_user_message, severity_matches)
            except Exception as exc:
                email_status = {
                    "sent": False,
                    "error": explain_email_error(exc),
                    "target_email": ALERT_EMAIL_TO,
                }

        alert_notice = ""
        if severity_matches:
            if email_status.get("sent"):
                alert_notice = (
                    f"{SEVERE_ALERT_MESSAGE} Alert sent to {mask_email(email_status.get('target_email'))}."
                )
            else:
                alert_notice = (
                    f"{SEVERE_ALERT_MESSAGE} Email could not be sent: {email_status.get('error', 'Unknown error')}."
                )

        save_chat_entry(user_message, bot_reply)
        return jsonify(
            {
                "reply": bot_reply,
                "severity": "severe" if severity_matches else "normal",
                "alert_notice": alert_notice,
            }
        )
    except Exception as exc:
        return jsonify(
            {
                "reply": "CareConnect hit a server error while processing your symptoms.",
                "severity": "error",
                "alert_notice": explain_email_error(exc),
            }
        ), 500
print("BEDROCK_MODEL_ID:", BEDROCK_MODEL_ID)                                                                                                                                       

if __name__ == "__main__":
    app.run()
