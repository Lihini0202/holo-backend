from celery import Celery
from google.cloud import vision
from supabase import create_client, Client
from dotenv import load_dotenv
import joblib
import pandas as pd
import re
import os
import hashlib
import ssl  

# --- 1. SETUP & CONFIGURATION ---
load_dotenv()

# REDIS URL from environment
redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

ssl_conf = {
    'ssl_cert_reqs': ssl.CERT_NONE
}


# Initialize Celery with the CORRECT URL immediately
celery_app = Celery(
    "holo_worker",
    broker=redis_url,
    backend=redis_url
)

# Apply SSL & Worker Settings
celery_app.conf.update(
    result_expires=3600,
    task_track_started=True,
    broker_connection_retry_on_startup=True,
    broker_use_ssl=ssl_conf,        # <--- Added SSL
    redis_backend_use_ssl=ssl_conf, # <--- Added SSL
    worker_pool='solo' 
)

# Database Setup
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = None

try:
    if SUPABASE_URL and SUPABASE_KEY:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("✅ Worker DB Connected")
except Exception as e:
    print(f"⚠️ Worker DB Error: {e}")

# Load ML Models
model = None
scaler = None
model_columns = None

try:
    model_dir = os.path.dirname(os.path.abspath(__file__))
    model_path = os.path.join(model_dir, 'ghosting_risk_model.pkl')

    print(f"Looking for model at: {model_path}")
    print(f"File exists: {os.path.exists(model_path)}")

    if os.path.exists(model_path):
        model = joblib.load(model_path)
        scaler = joblib.load(os.path.join(model_dir, 'scaler.pkl'))
        model_columns = joblib.load(os.path.join(model_dir, 'model_columns.pkl'))
        print("Worker ML Models Loaded OK")
    else:
        print("Model files not found - using fallback scoring")
except Exception as e:
    print(f"Worker Model Error (Using Dummy Mode): {e}")
    model = None

# --- 2. HELPER FUNCTIONS ---

def get_user_hash(name: str, age: str = "0", location: str = "unknown"):
    """Creates a unique signature using Name + Age + Location."""
    unique_string = f"{name.lower().strip()}|{age.strip()}|{location.lower().strip()}"
    return hashlib.sha256(unique_string.encode()).hexdigest()

def get_risk_label(score):
    if score < 0.30: return "Low Risk"
    elif score < 0.60: return "Medium Risk"
    else: return "High Risk"

def analyze_text_metrics(chat_text: str):
    lines = [line for line in chat_text.split('\n') if line.strip()]
    msg_count = len(lines)
    emoji_count = sum(1 for char in chat_text if ord(char) > 10000)
    emoji_rate = emoji_count / len(chat_text) if len(chat_text) > 0 else 0.0
    return msg_count, emoji_rate

def get_prediction(msg_count, emoji_rate):
    if not model: return 0.75
    try:
        input_df = pd.DataFrame(columns=model_columns)
        input_df.loc[0] = 0
        if 'Message_Sent_Count' in input_df.columns:
            input_df.loc[0, 'Message_Sent_Count'] = msg_count
        if 'Emoji_Usage_Rate' in input_df.columns:
            input_df.loc[0, 'Emoji_Usage_Rate'] = emoji_rate
            
        prediction = model.predict_proba(input_df)
        return prediction[0][1]
    except Exception as e:
        print(f"Prediction Error: {e}")
        return 0.5

def update_ledger(partner_name, risk_score, msg_count, emoji_rate, age="0", location="unknown", app_user_id=None):
    if not supabase: return "Database Offline"
    try:
        # 1. Identify the Partner (Ghost)
        partner_hash = get_user_hash(partner_name, age, location)
        
        # 2. Update Partner Profile (Public Record)
        existing = supabase.table("profiles").select("*").eq("user_hash", partner_hash).execute()
        
        if existing.data:
            profile = existing.data[0]
            new_count = profile['total_reports'] + 1
            new_avg = ((profile['avg_risk_score'] * profile['total_reports']) + float(risk_score)) / new_count
            
            supabase.table("profiles").update({
                "avg_risk_score": new_avg, 
                "total_reports": new_count,
                "last_seen": "now()"
            }).eq("user_hash", partner_hash).execute()
            history_msg = f"⚠️ Flagged {profile['total_reports']} times before."
        else:
            supabase.table("profiles").insert({
                "user_hash": partner_hash,
                "avg_risk_score": float(risk_score),
                "total_reports": 1,
                "last_seen": "now()",
                "first_name": partner_name, 
                "country": location,
                "age": int(age) if age.isdigit() else 0
            }).execute()
            history_msg = "First time tracked."
            
        # 3. Add Log Entry for the App User (Private Record)
        supabase.table("analysis_logs").insert({
            "user_hash": partner_hash,       # The Partner
            "auth_user_id": app_user_id,     # The App User (YOU)
            "message_count": msg_count,
            "emoji_count": int(emoji_rate * 100),
            "risk_score": float(risk_score),
            "actual_outcome": None
        }).execute()
        
        return history_msg
    except Exception as e:
        print(f"DB Update Error: {e}")
        return "History unavailable"

# --- 3. CELERY TASKS ---

@celery_app.task(name="analyze_text_task")
def analyze_text_task(chat_text, partner_name, age="0", location="unknown", user_id=None):
    print(f"Processing Text for {partner_name}...")
    
    # Analyze
    msg_count, emoji_rate = analyze_text_metrics(chat_text)
    risk_score = get_prediction(msg_count, emoji_rate)
    
    # Update Database
    history_msg = update_ledger(partner_name, risk_score, msg_count, emoji_rate, age, location, user_id)
    
    # Send Notification
    if user_id and supabase:
        try:
            supabase.table("notifications").insert({
                "user_hash": user_id,
                "title": "Analysis Ready 📊",
                "message": f"Risk Score for {partner_name}: {int(risk_score*100)}%.",
                "is_read": False
            }).execute()
        except Exception as e:
            print(f"Notification Error: {e}")
    
    return {
        "risk_score": float(risk_score),
        "status_label": get_risk_label(risk_score),
        "history_alert": history_msg,
        "extracted_data": {"messages": msg_count, "emoji_rate": round(emoji_rate, 2)}
    }

@celery_app.task(name="analyze_screenshot_task")
def analyze_screenshot_task(image_content, partner_name, age="0", location="unknown", user_id=None):
    print(f"Processing Screenshot for {partner_name}...")
    try:
        client = vision.ImageAnnotatorClient()
        image = vision.Image(content=image_content)
        response = client.text_detection(image=image)
        
        if not response.text_annotations:
            return {"error": "No text found in image"}
            
        full_text = response.text_annotations[0].description
        msg_count, emoji_rate = analyze_text_metrics(full_text)
        risk_score = get_prediction(msg_count, emoji_rate)
        
        history_msg = update_ledger(partner_name, risk_score, msg_count, emoji_rate, age, location, user_id)

        if user_id and supabase:
            try:
                supabase.table("notifications").insert({
                    "user_hash": user_id,
                    "title": "Analysis Ready 📊",
                    "message": f"Risk Score for {partner_name}: {int(risk_score*100)}%.",
                    "is_read": False
                }).execute()
            except Exception as e:
                print(f"Notification Error: {e}")
        
        return {
            "risk_score": float(risk_score),
            "status_label": get_risk_label(risk_score),
            "history_alert": history_msg,
            "extracted_data": {"messages": msg_count, "preview": full_text[:50]}
        }
    except Exception as e:
        return {"error": str(e)}