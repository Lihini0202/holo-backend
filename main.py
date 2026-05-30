from fastapi import FastAPI, HTTPException, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client, Client
from openai import OpenAI
from dotenv import load_dotenv
from celery.result import AsyncResult
import os
import json
import hashlib
import ssl  

# Celery tasks
from tasks import celery_app, analyze_screenshot_task, analyze_text_task

# ==========================================
# 1. CONFIGURATION & SETUP
# ==========================================

load_dotenv()


# Redis URL from environment
redis_url = os.getenv("REDIS_URL")

ssl_conf = {
    'ssl_cert_reqs': ssl.CERT_NONE
}

celery_app.conf.update(
    broker_url=redis_url,          # Force the correct URL
    result_backend=redis_url,      # Force the correct URL
    broker_use_ssl=ssl_conf,       # Force SSL
    redis_backend_use_ssl=ssl_conf,
    broker_connection_retry_on_startup=True,
    worker_concurrency=4
)
# -------------------------------------------------------------

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

app = FastAPI()

# --- ROOT ENDPOINT (To fix "Not Found" error) ---
@app.get("/")
def home():
    return {
        "status": "Online",
        "message": "Holo Backend is Running!",
        "docs_url": "/docs"
    }

supabase: Client = None
ai_client = None

try:
    if SUPABASE_URL and SUPABASE_KEY:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("Database Configured (API Side)!")
    else:
        print("Supabase Keys Missing in .env")

    if OPENROUTER_API_KEY:
        ai_client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=OPENROUTER_API_KEY,
        )
        print("AI Coach Configured!")
    else:
        print("OpenRouter Key Missing in .env")

except Exception as e:
    print(f"Config Error: {e}")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# 2. HELPER
# ==========================================

def get_user_hash(name: str, age: str = "0", location: str = "unknown"):
    unique_string = f"{name.lower().strip()}|{age.strip()}|{location.lower().strip()}"
    return hashlib.sha256(unique_string.encode()).hexdigest()

# ==========================================
# 3. ASYNC CELERY ENDPOINTS
# ==========================================

class TextSubmission(BaseModel):
    partner_name: str
    chat_text: str
    age: str = "0"
    location: str = "unknown"
    user_id: str         


@app.post("/analyze-screenshot")
async def analyze_screenshot(
    file: UploadFile = File(...),
    partner_name: str = Form(...),
    age: str = Form("0"),
    location: str = Form("unknown"),
    user_id: str = Form(...)
):
    """
    Sends screenshot + user_id to Celery worker.
    """
    try:
        content = await file.read()

        task = analyze_screenshot_task.delay(
            content,
            partner_name,
            age,
            location,
            user_id  
        )

        return {
            "status": "Processing",
            "task_id": task.id,
            "message": "Analysis started."
        }
    except Exception as e:
        return {"error": str(e)}


@app.post("/analyze-text")
def analyze_text_endpoint(submission: TextSubmission):
    """
    Sends chat text + user_id to Celery worker.
    """
    try:
        task = analyze_text_task.delay(
            submission.chat_text,
            submission.partner_name,
            submission.age,
            submission.location,
            submission.user_id 
        )

        return {
            "status": "Processing",
            "task_id": task.id
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/status/{task_id}")
def get_task_status(task_id: str):
    task_result = AsyncResult(task_id, app=celery_app)

    if task_result.ready():
        if task_result.successful():
            return {
                "status": "Completed",
                "result": task_result.result
            }
        else:
            return {
                "status": "Failed",
                "error": str(task_result.result)
            }
    else:
        return {"status": "Processing"}

# ==========================================
# 4. QUICK ENDPOINTS
# ==========================================

@app.post("/search-ghost")
def search_ghost(
    name: str = Form(...),
    age: str = Form("0"),
    location: str = Form("unknown")
):
    if not supabase:
        return {"status": "Error", "error": "Database offline"}

    try:
        user_hash = get_user_hash(name, age, location)
        response = supabase.table("profiles").select("*").eq("user_hash", user_hash).execute()

        if response.data:
            profile = response.data[0]
            return {
                "status": "Found",
                "reports": profile['total_reports'],
                "risk_score": profile['avg_risk_score'],
                "match_type": "Exact"
            }

        clean_name = name.strip()
        name_response = supabase.table("profiles").select("*").ilike("first_name", clean_name).execute()

        if name_response.data:
            profiles = name_response.data
            best_match = max(profiles, key=lambda p: p.get('total_reports', 0))

            return {
                "status": "Found (Similar Name)",
                "reports": best_match['total_reports'],
                "risk_score": best_match['avg_risk_score'],
                "match_type": "Partial",
                "note": (
                    f"Found {len(profiles)} users with similar name. "
                    f"Showing {best_match.get('first_name', '?')} "
                    f"({best_match.get('age', '?')}, {best_match.get('location', '?')})."
                )
            }

        return {"status": "Clean", "reports": 0, "risk_score": 0.0}

    except Exception as e:
        return {"status": "Error", "error": str(e)}


@app.post("/coach-reply")
async def coach_reply(draft: str = Form(...)):
    if ai_client is None:
        return {
            "risk_increase": 0.0,
            "advice": ["AI Config Missing."],
            "improved_draft": draft
        }

    prompt = """
    You are a dating coach. Analyze this draft message.
    Return JSON: {"risk_score": 0.1-0.9, "advice": "...", "improved_draft": "..."}
    """

    try:
        completion = ai_client.chat.completions.create(
            model="google/gemini-2.0-flash-001",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"Draft: {draft}"}
            ],
            response_format={"type": "json_object"}
        )

        data = json.loads(completion.choices[0].message.content)

        return {
            "risk_increase": data.get('risk_score', 0.5),
            "advice": [data.get('advice', "Add a question.")],
            "improved_draft": data.get('improved_draft', draft)
        }

    except Exception as e:
        return {
            "risk_increase": 0.0,
            "advice": [f"AI Error: {str(e)}"],
            "improved_draft": draft
        }