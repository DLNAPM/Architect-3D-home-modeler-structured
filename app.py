#!/usr/bin/env python3
"""
Architect 3D Home Modeler – Powered by Google AI (Corrected SDK)
- This version uses the correct and stable `vertexai.vision_models` SDK.
- This resolves the previous ImportError issues.
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

# --- MODIFIED --- Import the correct, stable Google Cloud libraries
import vertexai
from vertexai.vision_models import ImageGenerationModel

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

# Secret key
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY") or os.urandom(32)

# One-time init guard
app.config.setdefault("DB_INITIALIZED", False)
app.config.setdefault("FS_INITIALIZED", False)

# Email envs
MAIL_SERVER = os.getenv("MAIL_SERVER")
MAIL_PORT = int(os.getenv("MAIL_PORT") or "587")
MAIL_USERNAME = os.getenv("MAIL_USERNAME")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD")
MAIL_USE_TLS = os.getenv("MAIL_USE_TLS", "1") in ("1", "true", "True")
MAIL_DEFAULT_SENDER = os.getenv("MAIL_DEFAULT_SENDER") or f"no-reply@{APP_NAME.replace(' ', '').lower()}.local"

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
    "Front Exterior": {
        "Siding Material": ["Brick", "Stucco", "Fiber-cement", "Wood plank", "Stone veneer"],
        "Roof Style": ["Gable", "Hip", "Flat parapet", "Dutch gable", "Modern shed"],
        "Window Trim Color": ["Matte black", "Crisp white", "Bronze", "Charcoal gray", "Forest green"],
        "Landscaping": ["Boxwood hedges", "Desert xeriscape", "Lush tropical", "Minimalist gravel", "Cottage garden"],
        "Vehicle": ["None", "Luxury sedan", "Pickup truck", "SUV", "Sports car"],
        "Driveway Material": ["Concrete", "Pavers", "Gravel", "Stamped concrete", "Asphalt"],
        "Driveway Shape": ["Straight", "Curved", "Circular", "Side-load", "Split"],
        "Gate Style": ["No gate", "Modern slat", "Wrought iron", "Farm style", "Privacy panel"],
        "Garage Style": ["Single", "Double", "Carriage", "Glass-paneled", "Side-load"]
    },
    "Back Exterior": {
        "Siding Material": ["Brick", "Stucco", "Fiber-cement", "Wood plank", "Stone veneer"],
        "Roof Style": ["Gable", "Hip", "Flat parapet", "Dutch gable", "Modern shed"],
        "Window Trim Color": ["Matte black", "Crisp white", "Bronze", "Charcoal gray", "Forest green"],
        "Landscaping": ["Boxwood hedges", "Desert xeriscape", "Lush tropical", "Minimalist gravel", "Cottage garden"],
        "Swimming Pool": ["None", "Rectangular", "Freeform", "Infinity edge", "Lap pool"],
        "Paradise Grills": ["None", "Compact island", "L-shaped", "U-shaped", "Pergola bar"],
        "Basketball Court": ["None", "Half court", "Key only", "Sport tile pad", "Full court"],
        "Water Fountain": ["None", "Tiered stone", "Modern sheetfall", "Bubbling urns", "Pond with jets"],
        "Putting Green": ["None", "Single hole", "Two hole", "Wavy 3-hole", "Chipping fringe"]
    },
    "Living Room": {
        "Flooring": ["Wide oak", "Walnut herringbone", "Polished concrete", "Natural stone", "Eco bamboo"],
        "Wall Color": ["Warm white", "Greige", "Deep navy", "Sage", "Charcoal"],
        "Lighting": ["Recessed", "Chandelier", "Floor lamps", "Track", "Wall sconces"],
        "Furniture Style": ["Modern", "Transitional", "Traditional", "Scandinavian", "Industrial"],
        "Chairs": ["Lounge pair", "Wingback", "Accent swivel", "Mid-century", "Club chairs"],
        "Coffee Tables": ["Marble slab", "Glass oval", "Reclaimed wood", "Nested set", "Stone drum"],
        "Wine Storage": ["None", "Built-in wall", "Freestanding rack", "Glass wine room", "Under-stairs"],
        "Fireplace": ["No", "Yes"],
        "Door Style": ["French", "Pocket", "Barn", "Glass pivot", "Standard panel"]
    },
    "Kitchen": {
        "Flooring": ["Wide oak", "Walnut herringbone", "Polished concrete", "Porcelain tile", "Terrazzo"],
        "Wall Color": ["Warm white", "Greige", "Deep navy", "Sage", "Charcoal"],
        "Lighting": ["Recessed", "Linear pendant", "Island pendants", "Ceiling fixtures", "Under-cabinet"],
        "Cabinet Style": ["Shaker", "Flat-slab", "Inset", "Beaded", "Glass front"],
        "Countertops": ["Quartz", "Marble", "Granite", "Butcher block", "Concrete"],
        "Appliances": ["Stainless", "Panel-ready", "Black stainless", "Mixed metals", "Pro-grade"],
        "Backsplash": ["Subway", "Herringbone", "Slab stone", "Zellige", "Hex tile"],
        "Sink": ["Farmhouse", "Undermount SS", "Integrated stone", "Workstation", "Apron copper"],
        "Island Lights": ["Three pendants", "Linear bar", "Two globes", "Can lights", "Mixed fixtures"]
    },
    "Home Office": {
        "Flooring": ["Wide oak", "Carpet tile", "Polished concrete", "Cork", "Laminate"],
        "Wall Color": ["Warm white", "Greige", "Deep navy", "Sage", "Charcoal"],
        "Lighting": ["Task lamp", "Track", "Recessed", "Pendant", "Wall sconces"],
        "Desk Style": ["Standing", "Executive wood", "Minimalist metal", "L-shaped", "Dual sit-stand"],
        "Office Chair": ["Ergonomic mesh", "Leather executive", "Task chair", "Stool", "Kneeling"],
        "Storage": ["Open shelves", "Closed cabinets", "Mixed", "Credenza", "Wall system"]
    },
    "Primary Bedroom": {
        "Flooring": ["Plush carpet", "Wide oak", "Cork", "Laminate", "Engineered wood"],
        "Wall Color": ["Warm white", "Greige", "Deep navy", "Sage", "Charcoal"],
        "Lighting": ["Recessed", "Chandelier", "Wall sconces", "Ceiling fixture", "Bedside lamps"],
        "Bed Style": ["Upholstered", "Canopy", "Platform wood", "Metal frame", "Storage bed"],
        "Furniture Style": ["Modern", "Transitional", "Traditional", "Scandinavian", "Industrial"],
        "Closet Design": ["Reach-in", "Walk-in", "Wardrobe wall", "His/Hers", "Island closet"],
        "Ceiling Fan": ["None", "Modern", "Wood blade", "Industrial", "Retractable"]
    },
    "Primary Bathroom": {
        "Flooring": ["Porcelain tile", "Marble", "Terrazzo", "Natural stone", "Concrete"],
        "Wall Color": ["Warm white", "Greige", "Deep navy", "Sage", "Charcoal"],
        "Lighting": ["Sconces", "Backlit mirror", "Recessed", "Pendant", "Chandelier"],
        "Vanity Style": ["Floating", "Furniture look", "Double", "Open shelf", "Integrated"],
        "Shower or Tub": ["Large shower", "Freestanding tub", "Tub-shower", "Wet room", "Steam shower"],
        "Tile Style": ["Subway", "Hex", "Slab stone", "Zellige", "Mosaic"],
        "Bathroom Sink": ["Undermount", "Vessel", "Integrated", "Pedestal", "Trough"],
        "Mirror Style": ["Framed", "Backlit", "Arched", "Round", "Edge-lit"],
        "Balcony": ["No", "Yes"]
    },
    "Other Bedroom": {
        "Flooring": ["Plush carpet", "Wide oak", "Cork", "Laminate", "Engineered wood"],
        "Wall Color": ["Warm white", "Greige", "Deep navy", "Sage", "Charcoal"],
        "Lighting": ["Recessed", "Chandelier", "Wall sconces", "Ceiling fixture", "Bedside lamps"],
        "Bed Style": ["Upholstered", "Canopy", "Platform wood", "Metal frame", "Storage bed"],
        "Furniture Style": ["Modern", "Transitional", "Traditional", "Scandinavian", "Industrial"],
        "Ceiling Fan": ["None", "Modern", "Wood blade", "Industrial", "Retractable"],
        "Balcony": ["No", "Yes"]
    },
    "Half Bath": {
        "Flooring": ["Porcelain tile", "Marble", "Terrazzo", "Natural stone", "Concrete"],
        "Wall Color": ["Warm white", "Greige", "Deep navy", "Sage", "Charcoal"],
        "Lighting": ["Sconces", "Backlit mirror", "Recessed", "Pendant", "Chandelier"],
        "Vanity Style": ["Floating", "Furniture look", "Single", "Pedestal", "Console"],
        "Tile Style": ["Subway", "Hex", "Slab stone", "Zellige", "Mosaic"],
        "Mirror Style": ["Framed", "Backlit", "Arched", "Round", "Edge-lit"]
    },
    "Basement: Game Room": {
        "Flooring": ["Carpet tile", "Vinyl plank", "Cork", "Concrete stain", "Rubber tile"],
        "Wall Color": ["Warm white", "Greige", "Deep navy", "Sage", "Charcoal"],
        "Lighting": ["Track", "Recessed", "Neon accent", "Pendant", "Sconces"],
        "Pool Table": ["Classic wood", "Modern black", "Industrial", "Contemporary white", "Tournament"],
        "Wine Bar": ["None", "Back bar", "Wet bar", "Island bar", "Wall niche"],
        "Arcade Games": ["Pinball", "Racing", "Fighting", "Retro cabinets", "Skeeball"],
        "Other Table Games": ["Air hockey", "Foosball", "Shuffleboard", "Darts", "Poker"]
    },
    "Basement: Gym": {
        "Flooring": ["Rubber tile", "Vinyl plank", "Cork", "Foam mat", "Concrete seal"],
        "Wall Color": ["Warm white", "Greige", "Deep navy", "Sage", "Charcoal"],
        "Lighting": ["Track", "Recessed", "Neon accent", "Pendant", "Sconces"],
        "Equipment": ["Treadmill", "Bike", "Rowing", "Cable station", "Free weights"],
        "Gym Station": ["Smith machine", "Power rack", "Functional trainer", "Multi-gym", "Calisthenics"],
        "Steam Room": ["No", "Yes"]
    },
    "Basement: Theater Room": {
        "Flooring": ["Carpet tile", "Plush carpet", "Cork", "Laminate", "Acoustic floor"],
        "Wall Color": ["Warm white", "Charcoal", "Burgundy", "Navy", "Chocolate brown"],
        "Lighting": ["Step lights", "Wall sconces", "Star ceiling", "Recessed", "LED strips"],
        "Wall Treatment": ["Acoustic panels", "Fabric", "Wood slats", "Velvet", "Painted drywall"],
        "Seating": ["Recliners", "Sofas", "Stadium rows", "Bean bags", "Mixed"],
        "Popcorn Machine": ["No", "Yes"],
        "Sound System": ["5.1", "7.1", "Atmos", "Soundbar", "Hidden in-wall"],
        "Screen Type": ["Projector", "MicroLED", "OLED", "Ultra-short-throw", "Acoustically transparent"],
        "Movie Posters": ["No", "Yes"],
        "Show Movie": ["No", "Yes"]
    },
    "Basement: Hallway": {
        "Flooring": ["Carpet tile", "Vinyl plank", "Cork", "Concrete stain", "Rubber tile"],
        "Wall Color": ["Warm white", "Greige", "Deep navy", "Sage", "Charcoal"],
        "Lighting": ["Track", "Recessed", "Neon accent", "Pendant", "Sconces"],
        "Stairs": ["Open riser", "Closed", "Glass rail", "Wood rail", "Metal rail"]
    },
    "Family Room": {
        "Flooring": ["Wide oak", "Walnut herringbone", "Polished concrete", "Natural stone", "Eco bamboo"],
        "Wall Color": ["Warm white", "Greige", "Deep navy", "Sage", "Charcoal"],
        "Lighting": ["Recessed", "Chandelier", "Floor lamps", "Track", "Wall sconces"],
        "Furniture Style": ["Modern", "Transitional", "Traditional", "Scandinavian", "Industrial"],
        "Chairs": ["Lounge pair", "Wingback", "Accent swivel", "Mid-century", "Club chairs"]
    }
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

def generate_image_via_google_ai(prompt: str) -> str:
    """
    Generates an image using Google Cloud's Imagen model via the Vertex AI SDK.
    """
    if not GCP_PROJECT_ID:
        raise RuntimeError("GCP_PROJECT_ID environment variable not set.")

    vertexai.init(project=GCP_PROJECT_ID, location=GCP_LOCATION)
    
    model = ImageGenerationModel.from_pretrained("imagegeneration@006")
    
    response = model.generate_images(
        prompt=prompt,
        number_of_images=1,
        aspect_ratio="1:1"
    )
    
    if not response.images:
        raise RuntimeError("Google AI did not return any images.")

    # Access the raw image bytes directly from the response object
    image_bytes = response[0]._image_bytes
    return save_image_bytes(image_bytes)

# ---------- Routes ----------

@app.route("/")
def index():
    return render_template("index.html", app_name=APP_NAME, user=current_user(), basic_rooms=BASIC_ROOMS)

@app.post("/generate")
def generate():
    description = request.form.get("description", "").strip()
    plan_file = request.files.get("plan_file")
    plan_uploaded = bool(plan_file and plan_file.filename)
    if plan_uploaded:
        (UPLOAD_DIR / f"{uuid.uuid4().hex}_{plan_file.filename}").write_bytes(plan_file.read())

    session['available_rooms'] = build_room_list(description)
    user_id = session.get("user_id")
    new_rendering_ids = []
    
    conn = get_db()
    cur = conn.cursor()
    
    for subcat in ["Front Exterior", "Back Exterior"]:
        try:
            prompt = build_prompt(subcat, {}, description, plan_uploaded)
            rel_path = generate_image_via_google_ai(prompt)
            now = datetime.utcnow().isoformat()
            cur.execute("""
                INSERT INTO renderings (user_id, category, subcategory, options_json, prompt, image_path, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (user_id, "EXTERIOR", subcat, json.dumps({}), prompt, rel_path, now))
            conn.commit()
            new_rendering_ids.append(cur.lastrowid)
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

    flash("Generated Front & Back exterior renderings!", "success")
    return redirect(url_for("gallery" if user_id else "session_gallery"))

@app.post("/generate_room")
def generate_room():
    subcategory = request.form.get("subcategory")
    description = request.form.get("description", "")
    selected = {opt_name: request.form.get(opt_name) for opt_name in OPTIONS.get(subcategory, {}).keys()}
    prompt = build_prompt(subcategory, selected, description, False)
    
    try:
        rel_path = generate_image_via_google_ai(prompt)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    user_id = session.get("user_id")
    conn = get_db()
    cur = conn.cursor()
    now = datetime.utcnow().isoformat()
    cur.execute("""
        INSERT INTO renderings (user_id, category, subcategory, options_json, prompt, image_path, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (user_id, "ROOM", subcategory, json.dumps(selected), prompt, rel_path, now))
    conn.commit()
    new_id = cur.lastrowid
    conn.close()

    if not user_id:
        guest_ids = session.get('guest_rendering_ids', [])
        guest_ids.append(new_id)
        session['guest_rendering_ids'] = guest_ids
    
    return jsonify({"id": new_id, "path": url_for('static', filename=rel_path), "subcategory": subcategory, "message": f"Generated {subcategory} rendering!"})

@app.get("/gallery")
def gallery():
    user = current_user()
    if not user:
        return redirect(url_for('session_gallery'))

    conn = get_db()
    cur = conn.cursor()
    
    cur.execute("SELECT * FROM renderings WHERE user_id = ? ORDER BY created_at DESC", (user["id"],))
    all_items = [dict(row) for row in cur.fetchall()]
    conn.close()
    
    new_ids = session.pop('new_rendering_ids', [])
    new_items = [item for item in all_items if item['id'] in new_ids]
    main_items = [item for item in all_items if item['id'] not in new_ids]
    
    for item in all_items: item['options_dict'] = json.loads(item.get('options_json', '{}') or '{}')

    fav_count = sum(1 for r in main_items if r.get("favorited"))
    all_rooms = session.get('available_rooms', build_room_list(""))

    return render_template("gallery.html", app_name=APP_NAME, user=user, items=main_items,
                           new_items=new_items, show_slideshow=(fav_count >= 2),
                           rooms=all_rooms, options=OPTIONS)

@app.get("/session_gallery")
def session_gallery():
    user = current_user()
    if user:
        return redirect(url_for('gallery'))

    items = []
    guest_ids = session.get('guest_rendering_ids', [])
    if guest_ids:
        conn = get_db()
        cur = conn.cursor()
        q_marks = ",".join("?" for _ in guest_ids)
        cur.execute(f"SELECT * FROM renderings WHERE id IN ({q_marks}) ORDER BY created_at DESC", guest_ids)
        items = [dict(row) for row in cur.fetchall()]
        conn.close()
        
    for item in items: item['options_dict'] = json.loads(item.get('options_json', '{}') or '{}')
    
    all_rooms = session.get('available_rooms', build_room_list(""))

    return render_template("session_gallery.html", app_name=APP_NAME, user=user, items=items, 
                           options=OPTIONS, rooms=all_rooms)

@app.post("/bulk_action")
@login_required
def bulk_action():
    # Implementation for bulk actions
    pass

@app.get("/slideshow")
@login_required
def slideshow():
    # Implementation for user slideshow
    pass

@app.get("/session_slideshow")
def session_slideshow():
    guest_ids = session.get('guest_rendering_ids', [])
    if len(guest_ids) < 2:
        flash("You need at least two session renderings for a slideshow.", "info")
        return redirect(url_for('session_gallery'))
    
    conn = get_db()
    cur = conn.cursor()
    q_marks = ",".join("?" for _ in guest_ids)
    cur.execute(f"SELECT * FROM renderings WHERE id IN ({q_marks})", guest_ids)
    items = [dict(row) for row in cur.fetchall()]
    conn.close()

    return render_template("slideshow.html", app_name=APP_NAME, user=None, items=items)

@app.post("/modify_rendering/<int:rid>")
def modify_rendering(rid):
    description = request.form.get("description", "")
    conn = get_db()
    cur = conn.cursor()
    
    user_id = session.get("user_id")
    guest_ids = session.get('guest_rendering_ids', [])
    
    cur.execute("SELECT * FROM renderings WHERE id=?", (rid,))
    row = cur.fetchone()
    if not row:
        conn.close(); return jsonify({"error": "Rendering not found."}), 404
    
    if row['user_id'] != user_id and (user_id or row['id'] not in guest_ids):
        conn.close(); return jsonify({"error": "Permission denied."}), 403

    subcategory = row["subcategory"]
    original_options = json.loads(row["options_json"] or "{}")
    selected = {opt: request.form.get(opt) or original_options.get(opt) for opt in OPTIONS.get(subcategory, {}).keys()}

    prompt = build_prompt(subcategory, selected, description, False)
    try:
        rel_path = generate_image_via_google_ai(prompt)
    except Exception as e:
        conn.close(); return jsonify({"error": f"Modification failed: {e}"}), 500

    now = datetime.utcnow().isoformat()
    cur.execute("""
        INSERT INTO renderings (user_id, category, subcategory, options_json, prompt, image_path, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (user_id, row["category"], subcategory, json.dumps(selected), prompt, rel_path, now))
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    
    if not user_id:
        guest_ids.append(new_id)
        session['guest_rendering_ids'] = guest_ids

    return jsonify({"id": new_id, "path": url_for('static', filename=rel_path), "subcategory": subcategory, "message": f"Modified {subcategory} rendering!"})

# ---------- Auth Routes ----------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        name = (request.form.get("name") or "").strip()
        password = request.form.get("password") or ""
        if not email or not password:
            flash("Email and password are required.", "warning")
            return redirect(url_for("register"))
        
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id FROM users WHERE email = ?", (email,))
        if cur.fetchone():
            conn.close()
            flash("Email already registered.", "warning")
            return redirect(url_for("register"))
        
        pwd_hash = generate_password_hash(password)
        cur.execute("INSERT INTO users (email, name, password_hash, created_at) VALUES (?, ?, ?, ?)", (email, name, pwd_hash, datetime.utcnow().isoformat()))
        conn.commit()
        user_id = cur.lastrowid
        conn.close()
        
        session.clear()
        session["user_id"] = user_id
        session["user_email"] = email
        flash("Welcome! Account created.", "success")
        return redirect(url_for("gallery"))
    return render_template("register.html", app_name=APP_NAME, user=current_user())

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE email = ?", (email,))
        user = cur.fetchone()
        conn.close()
        
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["user_email"] = user["email"]
            flash("Logged in successfully.", "success")
            nxt = request.args.get("next")
            return redirect(nxt or url_for("gallery"))
        
        flash("Invalid credentials.", "danger")
        return redirect(url_for("login"))
    return render_template("login.html", app_name=APP_NAME, user=current_user())

@app.get("/logout")
def logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
