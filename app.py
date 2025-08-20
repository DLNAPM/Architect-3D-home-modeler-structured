#!/usr/bin/env python3
"""
Architect 3D Home Modeler – Powered by Google AI (Context-Aware Exteriors)
- Front and Back exteriors are now generated in sequence to ensure consistency.
- The Front Exterior is used as a reference image for generating the Back Exterior.
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

import vertexai
from vertexai.vision_models import ImageGenerationModel, Image

# ---------- Config ----------
APP_NAME = "Architect 3D Home Modeler"
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "architect.db"
UPLOAD_DIR = BASE_DIR / "uploads"
RENDER_DIR = BASE_DIR / "static" / "renderings"
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

# --- Google Cloud Project Configuration ---
GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID")
GCP_LOCATION = os.getenv("GCP_LOCATION", "us-central1")

# Create Flask app
app = Flask(__name__, template_folder=str(TEMPLATES_DIR), static_folder=str(STATIC_DIR))
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY") or os.urandom(32)
app.config.setdefault("DB_INITIALIZED", False)
app.config.setdefault("FS_INITIALIZED", False)

# ---------- Helpers ----------

def init_fs_once():
    """Create necessary directories if they don't exist."""
    if not app.config["FS_INITIALIZED"]:
        for p in [UPLOAD_DIR, RENDER_DIR, STATIC_DIR, TEMPLATES_DIR]:
            p.mkdir(parents=True, exist_ok=True)
        ico = STATIC_DIR / "favicon.ico"
        if not ico.exists():
            img = Image.new("RGBA", (32, 32)); d = ImageDraw.Draw(img)
            d.rectangle([4, 4, 28, 28], fill="#4a6dff"); d.text((8, 8), "A3D", fill="#fff")
            img.save(ico, format="ICO")
        app.config["FS_INITIALIZED"] = True

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db_once():
    """Initialize SQLite tables once."""
    if app.config["DB_INITIALIZED"]: return
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL, name TEXT,
        password_hash TEXT NOT NULL, created_at TEXT NOT NULL
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS renderings (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, category TEXT NOT NULL,
        subcategory TEXT NOT NULL, options_json TEXT, prompt TEXT NOT NULL,
        image_path TEXT NOT NULL, liked INTEGER DEFAULT 0, favorited INTEGER DEFAULT 0,
        created_at TEXT NOT NULL, FOREIGN KEY(user_id) REFERENCES users(id)
    )""")
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
    "Front Exterior": {"Siding Material": ["Brick", "Stucco", "Fiber-cement", "Wood plank", "Stone veneer"],"Roof Style": ["Gable", "Hip", "Flat parapet", "Dutch gable", "Modern shed"],"Window Trim Color": ["Matte black", "Crisp white", "Bronze", "Charcoal gray", "Forest green"],"Landscaping": ["Boxwood hedges", "Desert xeriscape", "Lush tropical", "Minimalist gravel", "Cottage garden"],"Vehicle": ["None", "Luxury sedan", "Pickup truck", "SUV", "Sports car"],"Driveway Material": ["Concrete", "Pavers", "Gravel", "Stamped concrete", "Asphalt"],"Driveway Shape": ["Straight", "Curved", "Circular", "Side-load", "Split"],"Gate Style": ["No gate", "Modern slat", "Wrought iron", "Farm style", "Privacy panel"],"Garage Style": ["Single", "Double", "Carriage", "Glass-paneled", "Side-load"]},
    "Back Exterior": {"Siding Material": ["Brick", "Stucco", "Fiber-cement", "Wood plank", "Stone veneer"],"Roof Style": ["Gable", "Hip", "Flat parapet", "Dutch gable", "Modern shed"],"Window Trim Color": ["Matte black", "Crisp white", "Bronze", "Charcoal gray", "Forest green"],"Landscaping": ["Boxwood hedges", "Desert xeriscape", "Lush tropical", "Minimalist gravel", "Cottage garden"],"Swimming Pool": ["None", "Rectangular", "Freeform", "Infinity edge", "Lap pool"],"Paradise Grills": ["None", "Compact island", "L-shaped", "U-shaped", "Pergola bar"],"Basketball Court": ["None", "Half court", "Key only", "Sport tile pad", "Full court"],"Water Fountain": ["None", "Tiered stone", "Modern sheetfall", "Bubbling urns", "Pond with jets"],"Putting Green": ["None", "Single hole", "Two hole", "Wavy 3-hole", "Chipping fringe"]},
    # ... (Other room options omitted for brevity)
}
BASIC_ROOMS = ["Living Room", "Kitchen", "Home Office", "Primary Bedroom", "Primary Bathroom", "Other Bedroom", "Half Bath", "Family Room"]
BASEMENT_ROOMS = ["Basement: Game Room", "Basement: Gym", "Basement: Theater Room", "Basement: Hallway"]

def build_room_list(description: str):
    rooms = BASIC_ROOMS.copy()
    if "basement" in (description or "").lower():
        rooms.extend(BASEMENT_ROOMS)
    return rooms

def build_prompt(subcategory: str, options_map: dict, description: str, reference_image=None):
    realism_command = "An ultra-realistic, professional architectural photograph of a residential home, emulating a shot taken on a Sony A7R IV with a sharp 35mm G Master prime lens. The lighting is soft, natural, golden hour light. The image must have a cinematic quality, with photorealistic textures (wood grain, concrete texture, glass reflections)."
    selections = ", ".join([f"{k} is {v}" for k, v in options_map.items() if v and v not in ["None", ""]])
    
    view_context = ""
    if subcategory == "Front Exterior":
        view_context = "This is an eye-level, street-level perspective of the front facade, clearly showing the driveway, garage, and front entrance."
        description = re.sub(r'swimming pool|pool', '', description, flags=re.IGNORECASE)
    elif subcategory == "Back Exterior":
        view_context = "Use the provided reference image of the front of the house as the primary guide for architectural style, materials, colors, and landscaping. The following rendering MUST be the back of the EXACT SAME HOUSE. This is an eye-level perspective from the backyard, with a focus on outdoor living areas like the patio or pool area."
    else:
        view_context = f"This is an interior view of the {subcategory}."

    prompt_parts = [
        realism_command,
        view_context,
        f"The architectural style is: {description.strip() or 'a tasteful contemporary design'}.",
        f"Key features include: {selections}." if selections else "The designer's choice of cohesive, high-end materials should be used."
    ]
    return " ".join(prompt_parts)

def save_image_bytes(png_bytes: bytes, return_path=False) -> str:
    uid = uuid.uuid4().hex
    filepath = RENDER_DIR / f"{uid}.png"
    with open(filepath, "wb") as f: f.write(png_bytes)
    return str(filepath) if return_path else f"renderings/{filepath.name}"

def generate_image_via_google_ai(prompt: str, reference_image: Image = None) -> str:
    if not GCP_PROJECT_ID:
        raise RuntimeError("GCP_PROJECT_ID environment variable not set.")

    vertexai.init(project=GCP_PROJECT_ID, location=GCP_LOCATION)
    
    model = ImageGenerationModel.from_pretrained("imagegeneration@006")
    
    if reference_image:
        response = model.edit_image(
            prompt=prompt,
            base_image=reference_image,
            # Additional parameters can be added if needed
        )
    else:
        response = model.generate_images(
            prompt=prompt,
            number_of_images=1,
            aspect_ratio="16:9"
        )
    
    if not response:
        raise RuntimeError("Google AI did not return any images.")

    image_bytes = response[0]._image_bytes
    return save_image_bytes(image_bytes)

# ---------- Routes ----------

@app.route("/")
def index():
    return render_template("index.html", app_name=APP_NAME, user=current_user(), basic_rooms=BASIC_ROOMS)

@app.post("/generate")
def generate():
    description = request.form.get("description", "").strip()
    session['available_rooms'] = build_room_list(description)
    user_id = session.get("user_id")
    new_rendering_ids = []
    conn = get_db()
    cur = conn.cursor()
    
    try:
        # --- Step 1: Generate Front Exterior ---
        front_prompt = build_prompt("Front Exterior", {}, description)
        front_rel_path = generate_image_via_google_ai(front_prompt)
        now = datetime.utcnow().isoformat()
        cur.execute("INSERT INTO renderings (user_id, category, subcategory, options_json, prompt, image_path, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",(user_id, "EXTERIOR", "Front Exterior", json.dumps({}), front_prompt, front_rel_path, now))
        conn.commit()
        front_id = cur.lastrowid
        new_rendering_ids.append(front_id)

        # --- Step 2: Generate Back Exterior using Front as Reference ---
        front_image_full_path = STATIC_DIR / front_rel_path
        reference_image = Image.load_from_file(front_image_full_path)
        
        back_prompt = build_prompt("Back Exterior", {}, description, reference_image=reference_image)
        back_rel_path = generate_image_via_google_ai(back_prompt, reference_image=reference_image)
        now = datetime.utcnow().isoformat()
        cur.execute("INSERT INTO renderings (user_id, category, subcategory, options_json, prompt, image_path, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",(user_id, "EXTERIOR", "Back Exterior", json.dumps({}), back_prompt, back_rel_path, now))
        conn.commit()
        back_id = cur.lastrowid
        new_rendering_ids.append(back_id)

    except Exception as e:
        conn.close()
        flash(str(e), "danger")
        return redirect(url_for("index"))
    
    conn.close()
    
    session['new_rendering_ids'] = new_rendering_ids
    if not user_id:
        guest_ids = session.get('guest_rendering_ids', [])
        guest_ids.extend(new_rendering_ids)
        session['guest_rendering_ids'] = guest_ids

    flash("Generated consistent Front & Back exterior renderings!", "success")
    return redirect(url_for("gallery" if user_id else "session_gallery"))

# ... (The rest of the routes and functions remain the same as the previous correct version)
# ... (generate_room, gallery, session_gallery, bulk_action, slideshows, modify_rendering, auth routes)

@app.post("/generate_room")
def generate_room():
    # This function remains unchanged as it doesn't need image-to-image context
    subcategory = request.form.get("subcategory")
    description = request.form.get("description", "")
    selected = {opt_name: request.form.get(opt_name) for opt_name in OPTIONS.get(subcategory, {}).keys()}
    prompt = build_prompt(subcategory, selected, description)
    try:
        rel_path = generate_image_via_google_ai(prompt)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    # ... (rest of the function is the same)
    pass # Placeholder for brevity

@app.get("/gallery")
def gallery():
    # This function remains unchanged
    pass # Placeholder for brevity

@app.get("/session_gallery")
def session_gallery():
    # This function remains unchanged
    pass # Placeholder for brevity

@app.post("/bulk_action")
@login_required
def bulk_action():
    # This function remains unchanged
    pass # Placeholder for brevity

@app.get("/slideshow")
@login_required
def slideshow():
    # This function remains unchanged
    pass # Placeholder for brevity

@app.get("/session_slideshow")
def session_slideshow():
    # This function remains unchanged
    pass # Placeholder for brevity

@app.post("/modify_rendering/<int:rid>")
def modify_rendering(rid):
    # This function can also be enhanced for context, but for now it's unchanged
    pass # Placeholder for brevity

@app.route("/register", methods=["GET", "POST"])
def register():
    # This function remains unchanged
    pass # Placeholder for brevity

@app.route("/login", methods=["GET", "POST"])
def login():
    # This function remains unchanged
    pass # Placeholder for brevity

@app.get("/logout")
def logout():
    # This function remains unchanged
    pass # Placeholder for brevity

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
