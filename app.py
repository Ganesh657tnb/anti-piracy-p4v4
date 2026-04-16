import os
import sqlite3
import streamlit as st
import tempfile
import subprocess
import numpy as np
from scipy.io import wavfile
import pandas as pd
import matplotlib.pyplot as plt

# ---------------- CONFIG ----------------
st.set_page_config("Guardian OTT: DSSS Anti-Piracy", layout="wide")

DB = "users.db"
VIDEO_DIR = "storage/videos"
os.makedirs(VIDEO_DIR, exist_ok=True)

# ---------------- DSSS CONSTANTS ----------------
BIT_SAMPLES = 22050
GAIN = 150.0
ID_BITS = 16

# ---------------- PN SEQUENCE ----------------
def get_pn_sequence(n, seed=42):
    np.random.seed(seed)
    return np.random.choice([-1, 1], size=n).astype(np.float32)

# ---------------- WAVEFORM ----------------
def plot_waveform(original, watermarked, sr):
    original = original / np.max(np.abs(original))
    watermarked = watermarked / np.max(np.abs(watermarked))
    N = 2000
    t = np.linspace(0, N/sr, N)
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(t, original[:N], label="Original")
    ax.plot(t, watermarked[:N], label="Watermarked", alpha=0.7)
    ax.legend()
    st.pyplot(fig)

# ---------------- CORRELATION ----------------
def plot_correlation(samples):
    pn = get_pn_sequence(BIT_SAMPLES)
    correlations = []
    for i in range(0, len(samples)-BIT_SAMPLES, BIT_SAMPLES):
        seg = samples[i:i+BIT_SAMPLES]
        correlations.append(np.sum(seg * pn))
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(correlations)
    st.pyplot(fig)

# ---------------- DATABASE ----------------
conn = sqlite3.connect(DB, check_same_thread=False)
c = conn.cursor()

c.execute("""
CREATE TABLE IF NOT EXISTS users(
id INTEGER PRIMARY KEY AUTOINCREMENT,
username TEXT UNIQUE,
password TEXT,
phone TEXT,
email TEXT)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS videos(
id INTEGER PRIMARY KEY AUTOINCREMENT,
filename TEXT,
path TEXT,
uploaded_by INTEGER)
""")

conn.commit()

# ---------------- WATERMARK ----------------
def embed_watermark(samples, user_id):
    samples = samples.astype(np.float32)
    bits = np.array(list(np.binary_repr(user_id, width=ID_BITS)), dtype=int)
    bits = bits * 2 - 1
    pn = get_pn_sequence(BIT_SAMPLES)

    frame_size = ID_BITS * BIT_SAMPLES
    num_frames = len(samples) // frame_size

    for f in range(num_frames):
        for i, b in enumerate(bits):
            start = f * frame_size + i * BIT_SAMPLES
            end = start + BIT_SAMPLES
            if end <= len(samples):
                samples[start:end] += b * pn * GAIN

    return np.clip(samples, -32768, 32767).astype(np.int16)

def extract_watermark(samples):
    samples = samples.astype(np.float32)
    pn = get_pn_sequence(BIT_SAMPLES)

    frame_size = ID_BITS * BIT_SAMPLES
    num_frames = len(samples) // frame_size

    recovered = []
    for f in range(num_frames):
        bits = ""
        for i in range(ID_BITS):
            start = f * frame_size + i * BIT_SAMPLES
            end = start + BIT_SAMPLES
            seg = samples[start:end]
            bits += "1" if np.sum(seg * pn) > 0 else "0"
        try:
            recovered.append(int(bits, 2))
        except:
            pass

    return max(set(recovered), key=recovered.count) if recovered else 0

# ---------------- FFMPEG ----------------
def extract_audio(video, wav):
    subprocess.run([
        "ffmpeg", "-y",
        "-i", video,
        "-vn",
        "-ac", "1",
        "-ar", "44100",
        "-acodec", "pcm_s16le",
        wav
    ], capture_output=True)

def merge_audio(video, wav, out):
    subprocess.run([
        "ffmpeg", "-y",
        "-i", video,
        "-i", wav,
        "-c:v", "copy",
        "-map", "0:v:0",
        "-map", "1:a:0",
        out
    ], capture_output=True)

# ---------------- SESSION ----------------
if "user" not in st.session_state:
    st.session_state.user = None

# ---------------- LOGIN ----------------
if not st.session_state.user:

    st.title("Guardian OTT Login")

    tab1, tab2 = st.tabs(["Login", "Register"])

    with tab1:
        u = st.text_input("Username")
        p = st.text_input("Password", type="password")
        if st.button("Login"):
            c.execute("SELECT id FROM users WHERE username=? AND password=?", (u, p))
            r = c.fetchone()
            if r:
                st.session_state.user = r[0]
                st.rerun()
            else:
                st.error("Invalid login")

    with tab2:
        ru = st.text_input("New Username")
        rp = st.text_input("New Password", type="password")
        rph = st.text_input("Phone")
        rem = st.text_input("Email")

        if st.button("Register"):
            try:
                c.execute(
                    "INSERT INTO users(username, password, phone, email) VALUES(?,?,?,?)",
                    (ru, rp, rph, rem))
                conn.commit()
                st.success("Registered successfully!")
            except:
                st.error("Username exists")

    st.stop()

# ---------------- MAIN ----------------
uid = st.session_state.user
tabs = st.tabs(["Upload", "Detect", "Library", "Users", "Logout"])

# ---------------- UPLOAD ----------------
with tabs[0]:
    st.header("Upload Video (No Watermark)")
    vid = st.file_uploader("Upload", type=["mp4", "mkv"])

    if vid and st.button("Store Video"):
        path = os.path.join(VIDEO_DIR, vid.name)
        with open(path, "wb") as f:
            f.write(vid.read())

        c.execute(
            "INSERT INTO videos(filename,path,uploaded_by) VALUES(?,?,?)",
            (vid.name, path, uid))
        conn.commit()

        st.success("Stored Successfully")
        st.video(path)

# ---------------- DETECT ----------------
with tabs[1]:
    st.header("Detect Piracy")

    v = st.file_uploader("Upload Suspicious Video", type=["mp4", "mkv"])

    if v and st.button("Scan"):
        with tempfile.TemporaryDirectory() as tmp:
            vid = os.path.join(tmp, v.name)
            wav = os.path.join(tmp, "d.wav")

            with open(vid, "wb") as f:
                f.write(v.read())

            extract_audio(vid, wav)
            sr, samples = wavfile.read(wav)

            plot_correlation(samples)

            wid = extract_watermark(samples)
            c.execute("SELECT username, phone FROM users WHERE id=?", (wid,))
            u = c.fetchone()

            if u:
                st.error(f"Piracy by {u[0]} (ID {wid})")
                st.warning(f"Phone: {u[1]}")
            else:
                st.success("No watermark detected")

# ---------------- LIBRARY ----------------
with tabs[2]:
    st.header("📚 Library")

    rows = pd.read_sql_query(
        """SELECT videos.id, videos.filename, videos.path, users.username
           FROM videos JOIN users ON users.id = videos.uploaded_by""",
        conn
    )

    for _, row in rows.iterrows():

        col1, col2, col3, col4 = st.columns([3,2,2,1])

        with col1:
            st.write(f"🎬 {row['filename']}")

        with col2:
            st.write(f"👤 {row['username']}")

        with col3:
            if os.path.exists(row["path"]):
                st.video(row["path"])
            else:
                st.warning("Missing file")

        with col4:
            if st.button("Prepare", key=f"prep_{row['id']}"):

                with tempfile.TemporaryDirectory() as tmp:
                    wav = os.path.join(tmp, "a.wav")
                    wm_wav = os.path.join(tmp, "wm.wav")
                    out = os.path.join(tmp, "wm.mp4")

                    extract_audio(row["path"], wav)
                    sr, samples = wavfile.read(wav)

                    wm_samples = embed_watermark(samples, uid)
                    wavfile.write(wm_wav, sr, wm_samples)

                    merge_audio(row["path"], wm_wav, out)

                    # ✅ STORE BYTES (FIX)
                    with open(out, "rb") as f:
                        st.session_state["video_bytes"] = f.read()

                    st.session_state["video_name"] = row["filename"]

    if "video_bytes" in st.session_state:
        st.download_button(
            "⬇️ Download",
            data=st.session_state["video_bytes"],
            file_name=st.session_state["video_name"],
            mime="video/mp4"
        )

# ---------------- USERS ----------------
with tabs[3]:
    st.header("Users")
    users = pd.read_sql_query("SELECT * FROM users", conn)
    st.dataframe(users)

# ---------------- LOGOUT ----------------
with tabs[4]:
    if st.button("Logout"):
        st.session_state.user = None
        st.rerun()
