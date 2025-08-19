#!/usr/bin/env python3
"""
Architect 3D Home Modeler – Powered by Google AI
- This version uses Google Cloud's Vertex AI (Imagen 2 model) for image generation.
- All HTML, CSS, and JS are in their respective folders.
"""

import os
import sqlite3
import uuid
import json
import base64
import re
from datetime import datetime
from functools import wraps
from pathlib import Path
from email.utils import formataddr

from flask import (
    Flask, request, render_template, redirect, url_for,
    flash, session, send_from_directory, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash
from PIL import Image, ImageDraw
from email.message import EmailMessage
import smtplib

# --- MODIFIED --- Import Google Cloud libraries
from google.cloud import aiplatform
from google.protobuf import json_format
from google.protobuf.struct_pb2 import Value

# ---------- Config ----------
APP_NAME = "Architect 3D Home Modeler"
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "architect.db"
UPLOAD_DIR = BASE_DIR / "uploads"
RENDER_DIR = BASE_DIR / "static" / "renderings"
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

# --- NEW --- Google Cloud Project Configuration
# Make sure these are set as environment variables on your server (e.g., Render)
GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID")
GCP_LOCATION = os.getenv("GCP_LOCATION", "us-central1") # e.g., us-central1

# Create Flask app
app = Flask(__name__, template_folder=str(TEMPLATES_DIR), static_folder=str(STATIC_DIR))

# Secret key
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY") or os.urandom(32)

# One-time init guard
app.config.setdefault("DB_INITIALIZED", False)
app.config.setdefault("FS_INITIALIZED", False)

# Email envs
MAIL_SERVER = os.getenv("MAIL_SERVER")
# ... (rest of the email config remains the same)

# ---------- Helpers ----------

def init_fs_once():
    """Create necessary directories if they don't exist."""
    if not app.config["FS_INITIALIZED"]:
        for p in [UPLOAD_DIR, RENDER_DIR, STATIC_DIR, TEMPLATES_DIR]:
            p.mkdir(parents=True, exist_ok=True)
        ico = STATIC_DIR / "favicon.ico"
        if not ico.exists():
            img = Image.new("RGBA", (32, 32))
            d = ImageDraw.Draw(img)
            d.rectangle([4, 4, 28, 28], fill="#4a6dff")
            d.text((8, 8), "A3D", fill="#fff")
            img.save(ico, format="ICO")
        app.config["FS_INITIALIZED"] = True

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db_once():
    """Initialize SQLite tables once."""
    if app.config["DB_INITIALIZED"]:
        return
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        name TEXT,
        password_hash TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS renderings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        category TEXT NOT NULL,
        subcategory TEXT NOT NULL,
        options_json TEXT,
        prompt TEXT NOT NULL,
        image_path TEXT NOT NULL,
        liked INTEGER DEFAULT 0,
        favorited INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """)
    conn.commit()
    conn.close()
    app.config["DB_INITIALIZED"] = True

@app.before_request
def before_request():
    init_fs_once()
    init_db_once()

def login_required(f):
    @wraps(f)
    def wrap(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to perform this action.", "warning")
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return wrap

def current_user():
    if "user_id" in session:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],))
        row = cur.fetchone()
        conn.close()
        return row
    return None

# ---------- Domain: Options & Prompting ----------
OPTIONS = {
    # (OPTIONS dictionary remains exactly the same)
}
BASIC_ROOMS = ["Living Room", "Kitchen", "Home Office", "Primary Bedroom", "Primary Bathroom", "Other Bedroom", "Half Bath", "Family Room"]
BASEMENT_ROOMS = ["Basement: Game Room", "Basement: Gym", "Basement: Theater Room", "Basement: Hallway"]

def build_room_list(description: str):
    """Dynamically creates a list of rooms based on the home description."""
    rooms = BASIC_ROOMS.copy()
    if "basement" in (description or "").lower():
        rooms.extend(BASEMENT_ROOMS)
    return rooms

def build_prompt(subcategory: str, options_map: dict, description: str, plan_uploaded: bool):
    """Builds a prompt optimized for Google's Imagen model."""
    
    # Imagen responds well to clear, descriptive sentences.
    realism_command = "A high-resolution, photorealistic architectural photograph of a residential home. The lighting is soft and natural, creating a warm and inviting atmosphere. The image has the quality of a professional magazine feature."
    selections = ", ".join([f"{k} is {v}" for k, v in options_map.items() if v and v not in ["None", ""]])
    
    view_context = ""
    if subcategory == "Front Exterior":
        view_context = f"This is a {subcategory} view from the street, clearly showing the driveway, garage, and front entrance."
        description = re.sub(r'swimming pool|pool', '', description, flags=re.IGNORECASE)
    elif subcategory == "Back Exterior":
        view_context = f"This is a {subcategory} view from the backyard, with a focus on outdoor living areas like the patio."
    else:
        view_context = f"This is an interior view of the {subcategory}."

    prompt_parts = [
        realism_command,
        view_context,
        f"The overall style is: {description.strip() or 'a tasteful contemporary design'}.",
        f"Specific features include: {selections}." if selections else "The designer's choice of cohesive, high-end materials should be used."
    ]
    return " ".join(prompt_parts)

def save_image_bytes(png_bytes: bytes) -> str:
    uid = uuid.uuid4().hex
    filepath = RENDER_DIR / f"{uid}.png"
    with open(filepath, "wb") as f: f.write(png_bytes)
    return f"renderings/{filepath.name}"

# --- REPLACED --- This function now uses the Google Cloud Vertex AI API
def generate_image_via_google_ai(prompt: str) -> str:
    """
    Generates an image using Google Cloud's Imagen 2 model via Vertex AI.
    """
    if not GCP_PROJECT_ID:
        raise RuntimeError("GCP_PROJECT_ID environment variable not set.")

    aiplatform.init(project=GCP_PROJECT_ID, location=GCP_LOCATION)
    
    # The specific model for image generation
    model = aiplatform.ImageGenerationModel.from_pretrained("imagegeneration@006")
    
    # The API call to generate images
    response = model.generate_images(
        prompt=prompt,
        number_of_images=1,
        # You can add more parameters here if needed, e.g., negative_prompt
    )
    
    if not response.images:
        raise RuntimeError("Google AI did not return any images.")

    # The image data is base64 encoded, so we decode it
    image_bytes = response.images[0]._image_bytes
    return save_image_bytes(image_bytes)

# ... (The rest of the routes and auth functions remain the same, but...)
# ... (we need to replace the call to the old generation function with the new one)

@app.post("/generate")
def generate():
    # ... (code to get description, etc.)
    
    for subcat in ["Front Exterior", "Back Exterior"]:
        try:
            prompt = build_prompt(subcat, {}, description, plan_uploaded)
            # --- MODIFIED --- Call the new Google AI function
            rel_path = generate_image_via_google_ai(prompt) 
            # ... (rest of the function)
        except Exception as e:
            # ... (error handling)
    # ... (rest of the function)
    pass # Placeholder, full code is identical except for the function call

@app.post("/generate_room")
def generate_room():
    # ... (code to get subcategory, description, etc.)
    prompt = build_prompt(subcategory, selected, description, False)
    try:
        # --- MODIFIED --- Call the new Google AI function
        rel_path = generate_image_via_google_ai(prompt)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    # ... (rest of the function)
    pass # Placeholder, full code is identical except for the function call

@app.post("/modify_rendering/<int:rid>")
def modify_rendering(rid):
    # ... (code to get rendering details)
    prompt = build_prompt(subcategory, selected, description, False)
    try:
        # --- MODIFIED --- Call the new Google AI function
        rel_path = generate_image_via_google_ai(prompt)
    except Exception as e:
        return jsonify({"error": f"Modification failed: {e}"}), 500
    # ... (rest of the function)
    pass # Placeholder, full code is identical except for the function call

# (All other routes, auth functions, and the main execution block are identical to the previous version)
