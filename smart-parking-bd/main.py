"""
Smart Parking BD — Backend API (FastAPI + SQLite) — RENDER EDITION
=====================================================================
Render-compatible version with:
- Dynamic PORT environment variable support
- Proper static file serving
- Multi-origin CORS
"""

import sqlite3
import time
import random
import string
import hashlib
import json
import os
from contextlib import contextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, List

DB_PATH = "parking.db"

app = FastAPI(title="Smart Parking BD API")

# CORS — সব origin থেকে (Render + বাইরের ডিভাইস সব)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"

SLOT_CONFIG = [
    {"id": "A1", "zone": "A", "type": "car"},
    {"id": "A2", "zone": "A", "type": "car"},
    {"id": "A3", "zone": "A", "type": "car"},
    {"id": "A4", "zone": "A", "type": "suv"},
    {"id": "B1", "zone": "B", "type": "car"},
    {"id": "B2", "zone": "B", "type": "car"},
    {"id": "B3", "zone": "B", "type": "motorcycle"},
    {"id": "B4", "zone": "B", "type": "motorcycle"},
    {"id": "C1", "zone": "C", "type": "car"},
    {"id": "C2", "zone": "C", "type": "car"},
    {"id": "C3", "zone": "C", "type": "suv"},
    {"id": "C4", "zone": "C", "type": "truck"},
]
TYPE_PRICE = {"car": 50, "suv": 80, "motorcycle": 20, "truck": 100}


# =====================================================
# DATABASE SETUP
# =====================================================
@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS slots (
                id TEXT PRIMARY KEY,
                zone TEXT NOT NULL,
                type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'available',
                booked_by TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                phone TEXT UNIQUE NOT NULL,
                email TEXT,
                pass_hash TEXT NOT NULL,
                created_at INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id TEXT PRIMARY KEY,
                slot_id TEXT NOT NULL,
                slot_zone TEXT,
                slot_type TEXT,
                car_number TEXT,
                owner_name TEXT,
                owner_phone TEXT,
                duration INTEGER,
                price INTEGER,
                payment_method TEXT,
                status TEXT DEFAULT 'confirmed',
                created_at INTEGER,
                expires_at INTEGER,
                face_verified INTEGER DEFAULT 0,
                user_id TEXT,
                user_name TEXT
            )
        """)
        cur = conn.execute("SELECT COUNT(*) as c FROM slots")
        if cur.fetchone()["c"] == 0:
            for s in SLOT_CONFIG:
                conn.execute(
                    "INSERT INTO slots (id, zone, type, status, booked_by) VALUES (?,?,?,?,?)",
                    (s["id"], s["zone"], s["type"], "available", None),
                )


init_db()


def hash_pass(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def row_to_dict(row):
    return dict(row) if row else None


def gen_booking_id():
    return "BK" + str(int(time.time()))[-8:] + "".join(random.choices(string.ascii_uppercase + string.digits, k=3))


def gen_user_id():
    return "U" + str(int(time.time() * 1000))[-10:]


# =====================================================
# AUTO-EXPIRE
# =====================================================
def expire_old_bookings(conn):
    now = int(time.time() * 1000)
    rows = conn.execute(
        "SELECT * FROM bookings WHERE status='confirmed' AND expires_at <= ?", (now,)
    ).fetchall()
    for b in rows:
        conn.execute("UPDATE bookings SET status='expired' WHERE id=?", (b["id"],))
        conn.execute(
            "UPDATE slots SET status='available', booked_by=NULL WHERE id=? AND booked_by=?",
            (b["slot_id"], b["id"]),
        )


# =====================================================
# Pydantic Models
# =====================================================
class RegisterBody(BaseModel):
    name: str
    phone: str
    email: Optional[str] = ""
    password: str

class LoginBody(BaseModel):
    phone: str
    password: str

class AdminLoginBody(BaseModel):
    username: str
    password: str

class BookingCreateBody(BaseModel):
    slot_id: str
    car_number: str
    owner_name: str
    owner_phone: str
    duration: int
    payment_method: str
    face_verified: bool
    user_id: str
    user_name: str

class SlotToggleBody(BaseModel):
    slot_id: str


# =====================================================
# ROUTES
# =====================================================
@app.get("/api/state")
def get_state():
    with get_db() as conn:
        expire_old_bookings(conn)
        slots = [row_to_dict(r) for r in conn.execute("SELECT * FROM slots ORDER BY id").fetchall()]
        bookings = [row_to_dict(r) for r in conn.execute("SELECT * FROM bookings ORDER BY created_at DESC").fetchall()]
        user_count = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
        return {"slots": slots, "bookings": bookings, "user_count": user_count, "server_time": int(time.time() * 1000)}


@app.get("/api/health")
def health_check():
    return {"status": "ok", "message": "Smart Parking BD API is running"}


# =====================================================
# AUTH
# =====================================================
@app.post("/api/register")
def register(body: RegisterBody):
    if len(body.phone) != 11:
        raise HTTPException(400, "সঠিক ফোন নম্বর দিন (১১ সংখ্যা)")
    if len(body.password) < 6:
        raise HTTPException(400, "পাসওয়ার্ড কমপক্ষে ৬ অক্ষর হতে হবে")
    with get_db() as conn:
        existing = conn.execute("SELECT id FROM users WHERE phone=?", (body.phone,)).fetchone()
        if existing:
            raise HTTPException(409, "এই নম্বরে আগেই অ্যাকাউন্ট আছে!")
        uid = gen_user_id()
        conn.execute(
            "INSERT INTO users (id, name, phone, email, pass_hash, created_at) VALUES (?,?,?,?,?,?)",
            (uid, body.name, body.phone, body.email, hash_pass(body.password), int(time.time() * 1000)),
        )
        return {"id": uid, "name": body.name, "phone": body.phone, "email": body.email}


@app.post("/api/login")
def login(body: LoginBody):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE phone=?", (body.phone,)).fetchone()
        if not row or row["pass_hash"] != hash_pass(body.password):
            raise HTTPException(401, "ফোন নম্বর বা পাসওয়ার্ড ভুল!")
        return {"id": row["id"], "name": row["name"], "phone": row["phone"], "email": row["email"]}


@app.post("/api/admin-login")
def admin_login(body: AdminLoginBody):
    if body.username == ADMIN_USERNAME and body.password == ADMIN_PASSWORD:
        return {"ok": True}
    raise HTTPException(401, "ভুল ইউজারনেম বা পাসওয়ার্ড!")


# =====================================================
# BOOKING
# =====================================================
@app.post("/api/book")
def create_booking(body: BookingCreateBody):
    with get_db() as conn:
        expire_old_bookings(conn)
        slot = conn.execute("SELECT * FROM slots WHERE id=?", (body.slot_id,)).fetchone()
        if not slot:
            raise HTTPException(404, "স্লট পাওয়া যায়নি")
        if slot["status"] != "available":
            raise HTTPException(409, "SLOT_TAKEN")

        if not body.face_verified:
            raise HTTPException(400, "ফেস ভেরিফিকেশন প্রয়োজন")

        price = TYPE_PRICE.get(slot["type"], 50) * body.duration
        booking_id = gen_booking_id()
        now = int(time.time() * 1000)
        expires_at = now + body.duration * 3600 * 1000

        conn.execute(
            """INSERT INTO bookings
               (id, slot_id, slot_zone, slot_type, car_number, owner_name, owner_phone,
                duration, price, payment_method, status, created_at, expires_at, face_verified, user_id, user_name)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (booking_id, body.slot_id, slot["zone"], slot["type"], body.car_number.upper(),
             body.owner_name, body.owner_phone, body.duration, price, body.payment_method,
             "confirmed", now, expires_at, 1 if body.face_verified else 0, body.user_id, body.user_name),
        )
        conn.execute("UPDATE slots SET status='booked', booked_by=? WHERE id=?", (booking_id, body.slot_id))

        booking = row_to_dict(conn.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone())
        return booking


@app.post("/api/cancel-booking/{booking_id}")
def cancel_booking(booking_id: str):
    with get_db() as conn:
        b = conn.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
        if not b:
            raise HTTPException(404, "বুকিং পাওয়া যায়নি")
        conn.execute("UPDATE bookings SET status='cancelled' WHERE id=?", (booking_id,))
        conn.execute(
            "UPDATE slots SET status='available', booked_by=NULL WHERE id=? AND booked_by=?",
            (b["slot_id"], booking_id),
        )
        return {"ok": True}


# =====================================================
# ADMIN
# =====================================================
@app.post("/api/admin/toggle-slot")
def toggle_slot(body: SlotToggleBody):
    with get_db() as conn:
        slot = conn.execute("SELECT * FROM slots WHERE id=?", (body.slot_id,)).fetchone()
        if not slot:
            raise HTTPException(404, "স্লট পাওয়া যায়নি")
        new_status = "booked" if slot["status"] == "available" else "available"
        booked_by = "ADMIN" if new_status == "booked" else None
        conn.execute("UPDATE slots SET status=?, booked_by=? WHERE id=?", (new_status, booked_by, body.slot_id))
        return {"id": body.slot_id, "status": new_status}


@app.post("/api/admin/reset-slots")
def reset_slots():
    with get_db() as conn:
        slots = conn.execute("SELECT * FROM slots").fetchall()
        for s in slots:
            active_booking = None
            if s["booked_by"]:
                active_booking = conn.execute(
                    "SELECT id FROM bookings WHERE id=? AND status='confirmed'", (s["booked_by"],)
                ).fetchone()
            if not active_booking:
                conn.execute("UPDATE slots SET status='available', booked_by=NULL WHERE id=?", (s["id"],))
        return {"ok": True}


@app.get("/api/admin/export-csv")
def export_csv():
    with get_db() as conn:
        bookings = conn.execute("SELECT * FROM bookings ORDER BY created_at DESC").fetchall()
        lines = ["বুকিং আইডি,স্লট,গাড়ি নম্বর,মালিক,ফোন,সময়কাল,মোট,পেমেন্ট,স্ট্যাটাস"]
        for b in bookings:
            lines.append(
                f'"{b["id"]}","{b["slot_id"]}","{b["car_number"]}","{b["owner_name"]}",'
                f'"{b["owner_phone"]}","{b["duration"]}ঘণ্টা","{b["price"]}","{b["payment_method"]}","{b["status"]}"'
            )
        csv_content = "\ufeff" + "\n".join(lines)
        from fastapi.responses import Response
        return Response(content=csv_content, media_type="text/csv",
                         headers={"Content-Disposition": "attachment; filename=bookings.csv"})


# =====================================================
# STATIC FILES — IMPORTANT: Check if static/ exists
# =====================================================
static_dir = "static"
if os.path.isdir(static_dir):
    print(f"[Startup] Mounting static files from: {os.path.abspath(static_dir)}")
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
else:
    print(f"[Startup] WARNING: {static_dir}/ directory not found!")


@app.get("/")
def serve_index():
    index_path = os.path.join(static_dir, "index.html")
    if os.path.isfile(index_path):
        return FileResponse(index_path)
    return {"error": "index.html not found. Check your static/ directory."}


@app.get("/{filename}")
def serve_root_files(filename: str):
    allowed = {"style.css", "app.js", "manifest.json"}
    if filename in allowed:
        path = os.path.join(static_dir, filename)
        if os.path.isfile(path):
            return FileResponse(path)
    raise HTTPException(404, "Not found")


# =====================================================
# MAIN — Render-compatible
# =====================================================
if __name__ == "__main__":
    import uvicorn
    
    # Render sets PORT environment variable — use it
    port = int(os.environ.get("PORT", 8000))
    host = "0.0.0.0"
    
    print(f"\n[Startup] Starting Smart Parking BD API")
    print(f"[Startup] Host: {host}")
    print(f"[Startup] Port: {port}")
    print(f"[Startup] Database: {DB_PATH}")
    print(f"[Startup] CORS: Enabled for all origins\n")
    
    uvicorn.run(app, host=host, port=port