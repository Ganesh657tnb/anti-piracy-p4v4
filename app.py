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
GAIN        = 150.0
ID_BITS     = 16
REDUNDANCY  = 5     # how many times the ID is embedded across the audio


# ---------------- PN SEQUENCE ----------------
def get_pn_sequence(n, seed=42):
    np.random.seed(seed)
    return np.random.choice([-1, 1], size=n).astype(np.float32)


# ---------------- WAVEFORM VISUALIZATION ----------------
def plot_waveform(original, watermarked, sr):
    orig = original.astype(np.float32)
    wm   = watermarked.astype(np.float32)
    if np.max(np.abs(orig)) > 0:
        orig /= np.max(np.abs(orig))
    if np.max(np.abs(wm)) > 0:
        wm   /= np.max(np.abs(wm))
    N  = 2000
    t  = np.linspace(0, N / sr, N)
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(t, orig[:N], label="Original Waveform")
    ax.plot(t, wm[:N],   label="Watermarked Waveform", alpha=0.7)
    ax.set_title("Audio Waveform — Zoomed View")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Amplitude")
    ax.legend()
    st.pyplot(fig)


# ---------------- CORRELATION GRAPH ----------------
def plot_correlation(samples):
    pn           = get_pn_sequence(BIT_SAMPLES)
    correlations = []
    for i in range(0, len(samples) - BIT_SAMPLES, BIT_SAMPLES):
        seg  = samples[i : i + BIT_SAMPLES].astype(np.float32)
        correlations.append(np.sum(seg * pn))
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(correlations)
    ax.set_title("DSSS Correlation Detection")
    ax.set_xlabel("Frame Index")
    ax.set_ylabel("Correlation Value")
    st.pyplot(fig)


# ---------------- DATABASE ----------------
conn = sqlite3.connect(DB, check_same_thread=False)
c    = conn.cursor()

c.execute("""
CREATE TABLE IF NOT EXISTS users (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE,
    password TEXT,
    phone    TEXT,
    email    TEXT
)""")

try:
    c.execute("ALTER TABLE users ADD COLUMN email TEXT")
    conn.commit()
except Exception:
    pass

c.execute("""
CREATE TABLE IF NOT EXISTS videos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    filename    TEXT,
    path        TEXT,
    uploaded_by INTEGER
)""")

conn.commit()


# ================================================================
#  WATERMARK EMBED  — embeds user_id with REDUNDANCY copies
# ================================================================
def embed_watermark(samples, user_id):
    """
    Spread REDUNDANCY copies of the watermark evenly across the audio.
    Each copy = ID_BITS DSSS-modulated chips of length BIT_SAMPLES.
    """
    samples    = samples.astype(np.float32)
    bits_raw   = np.array(list(np.binary_repr(user_id, width=ID_BITS)), dtype=int)
    bits       = (bits_raw * 2 - 1).astype(np.float32)   # 0 → -1,  1 → +1
    pn         = get_pn_sequence(BIT_SAMPLES)
    block_size = ID_BITS * BIT_SAMPLES                    # samples for one ID copy
    total_len  = len(samples)

    # Space copies evenly; guard against very short audio
    step = max(block_size, total_len // (REDUNDANCY + 1))

    for rep in range(REDUNDANCY):
        offset = step * (rep + 1) - block_size // 2
        offset = max(0, min(offset, total_len - block_size))
        for i, b in enumerate(bits):
            s = offset + i * BIT_SAMPLES
            e = s + BIT_SAMPLES
            if e <= total_len:
                samples[s:e] += b * pn * GAIN

    return np.clip(samples, -32768, 32767).astype(np.int16)


# ================================================================
#  WATERMARK EXTRACT  — majority vote over all detected copies
# ================================================================
def extract_watermark(samples):
    samples    = samples.astype(np.float32)
    pn         = get_pn_sequence(BIT_SAMPLES)
    block_size = ID_BITS * BIT_SAMPLES
    recovered  = []

    for start in range(0, len(samples) - block_size + 1, block_size):
        bits = ""
        for i in range(ID_BITS):
            s    = start + i * BIT_SAMPLES
            e    = s + BIT_SAMPLES
            corr = np.sum(samples[s:e] * pn)
            bits += "1" if corr > 0 else "0"
        try:
            recovered.append(int(bits, 2))
        except Exception:
            pass

    if not recovered:
        return 0
    return max(set(recovered), key=recovered.count)


# ---------------- FFMPEG HELPERS ----------------
def extract_audio(video_path, wav_path):
    subprocess.run([
        "ffmpeg", "-y", "-i", video_path,
        "-vn", "-ac", "1", "-ar", "44100",
        "-acodec", "pcm_s16le", wav_path
    ], capture_output=True)


def merge_audio(video_path, wav_path, out_path):
    subprocess.run([
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", wav_path,
        "-c:v", "copy",
        "-map", "0:v:0",
        "-map", "1:a:0",
        out_path
    ], capture_output=True)


# ================================================================
#  SESSION STATE
# ================================================================
if "user_id"  not in st.session_state:
    st.session_state.user_id  = None
if "username" not in st.session_state:
    st.session_state.username = None


# ================================================================
#  AUTH SCREEN
# ================================================================
if not st.session_state.user_id:

    st.title("🛡️ Guardian OTT — DSSS Anti-Piracy Platform")
    login_tab, reg_tab = st.tabs(["Login", "Register"])

    # ---------- LOGIN ----------
    with login_tab:
        st.subheader("Login to your account")
        l_user = st.text_input("Username", key="l_user")
        l_pass = st.text_input("Password", type="password", key="l_pass")

        if st.button("Login", key="btn_login"):
            if not l_user or not l_pass:
                st.error("Please enter both username and password.")
            else:
                c.execute(
                    "SELECT id, username FROM users WHERE username=? AND password=?",
                    (l_user, l_pass)
                )
                row = c.fetchone()
                if row:
                    st.session_state.user_id  = row[0]
                    st.session_state.username = row[1]
                    st.rerun()
                else:
                    st.error("Invalid username or password.")

    # ---------- REGISTER ----------
    with reg_tab:
        st.subheader("Create a new account")
        r_user  = st.text_input("Username",      key="r_user")
        r_pass  = st.text_input("Password",      type="password", key="r_pass")
        r_phone = st.text_input("Phone Number",  key="r_phone")
        r_email = st.text_input("Email Address", key="r_email")

        if st.button("Register", key="btn_register"):
            if not r_user or not r_pass or not r_phone or not r_email:
                st.error("All fields are required.")
            elif "@" not in r_email or "." not in r_email:
                st.error("Please enter a valid email address.")
            else:
                try:
                    c.execute(
                        "INSERT INTO users(username, password, phone, email) VALUES(?,?,?,?)",
                        (r_user, r_pass, r_phone, r_email)
                    )
                    conn.commit()
                    c.execute("SELECT id FROM users WHERE username=?", (r_user,))
                    new_id = c.fetchone()[0]
                    st.success(
                        f"✅ Account created! Your unique **User ID is {new_id}**. "
                        f"Please switch to the Login tab."
                    )
                except Exception:
                    st.error("Username already taken. Please choose another.")

    st.stop()


# ================================================================
#  MAIN APP
# ================================================================
uid   = st.session_state.user_id
uname = st.session_state.username

st.sidebar.markdown(f"### 👤 {uname}")
st.sidebar.caption(f"User ID: `{uid}`")

tabs = st.tabs([
    "📤 Upload",
    "🔍 Detect Piracy",
    "📚 Library",
    "👥 Users",
    "🚪 Logout"
])


# ================================================================
#  TAB 0 — UPLOAD
#  Store the raw video as-is (NO watermark at upload time)
# ================================================================
with tabs[0]:
    st.header("📤 Upload Video")
    st.info(
        "Upload a video to share it in the library. "
        "The file is stored as-is — **no watermark is added at upload time**. "
        "Each downloader will receive a copy watermarked with their own unique User ID."
    )

    uploaded = st.file_uploader("Choose a video file", type=["mp4", "mkv"])

    if uploaded and st.button("Upload Video"):
        with st.spinner("Saving to library…"):
            save_path = os.path.join(
                VIDEO_DIR, f"raw_{uid}_{uploaded.name}"
            )
            with open(save_path, "wb") as f:
                f.write(uploaded.read())

            c.execute(
                "INSERT INTO videos(filename, path, uploaded_by) VALUES(?,?,?)",
                (uploaded.name, save_path, uid)
            )
            conn.commit()

        st.success(f"✅ **{uploaded.name}** is now available in the library!")


# ================================================================
#  TAB 1 — DETECT PIRACY
# ================================================================
with tabs[1]:
    st.header("🔍 Detect Piracy")
    st.info(
        "Upload a suspected pirated video. The DSSS watermark embedded "
        "at download time will be extracted to identify the leaker."
    )

    suspect = st.file_uploader(
        "Upload suspicious video", type=["mp4", "mkv"], key="detect_up"
    )

    if suspect and st.button("Scan for Watermark"):
        with st.spinner("Extracting and analysing audio…"):
            with tempfile.TemporaryDirectory() as tmp:
                vpath = os.path.join(tmp, suspect.name)
                wpath = os.path.join(tmp, "detect.wav")
                with open(vpath, "wb") as f:
                    f.write(suspect.read())
                extract_audio(vpath, wpath)
                sr, samples = wavfile.read(wpath)

        st.subheader("DSSS Correlation Graph")
        plot_correlation(samples)

        detected_id = extract_watermark(samples)
        c.execute(
            "SELECT username, phone, email FROM users WHERE id=?",
            (detected_id,)
        )
        result = c.fetchone()

        if result and detected_id != 0:
            st.error(
                f"🚨 **Piracy Detected!**  "
                f"Watermark matches **{result[0]}** (User ID: `{detected_id}`)"
            )
            st.warning(f"📞 Phone: {result[1]}   |   📧 Email: {result[2]}")
        else:
            st.success("✅ No recognisable watermark detected in this video.")


# ================================================================
#  TAB 2 — LIBRARY
#  On download: extract audio → embed DOWNLOADER's uid → merge → serve
# ================================================================
with tabs[2]:
    st.header("📚 Video Library")

    rows = pd.read_sql_query(
        """
        SELECT  videos.id,
                videos.filename,
                videos.path,
                users.username AS uploader
        FROM    videos
        JOIN    users ON users.id = videos.uploaded_by
        """,
        conn
    )

    if rows.empty:
        st.info("No videos in the library yet. Upload one in the Upload tab!")
    else:
        for _, row in rows.iterrows():
            video_path = row["path"]

            with st.container():
                col1, col2, col3, col4 = st.columns([3, 2, 1, 1])

                with col1:
                    st.markdown(f"🎬 **{row['filename']}**")

                with col2:
                    st.markdown(f"👤 Uploaded by `{row['uploader']}`")

                with col3:
                    if os.path.exists(video_path):
                        with st.expander("▶ Preview"):
                            st.video(video_path)
                    else:
                        st.warning("File missing")

                with col4:
                    if os.path.exists(video_path):
                        # ── STEP 1: trigger processing ──
                        if st.button("⬇️ Download", key=f"dl_btn_{row['id']}"):
                            with st.spinner(
                                "Embedding your unique watermark… please wait."
                            ):
                                with tempfile.TemporaryDirectory() as tmp:
                                    raw_wav = os.path.join(tmp, "audio.wav")
                                    wm_wav  = os.path.join(tmp, "wm.wav")
                                    out_vid = os.path.join(
                                        tmp, f"wm_{row['filename']}"
                                    )

                                    # 1. Pull raw audio out of the stored video
                                    extract_audio(video_path, raw_wav)

                                    # 2. Load samples
                                    sr, samples = wavfile.read(raw_wav)

                                    # 3. Embed DOWNLOADER's uid with redundancy
                                    wm_samples = embed_watermark(samples, uid)

                                    # 4. Save watermarked audio
                                    wavfile.write(wm_wav, sr, wm_samples)

                                    # 5. Merge back into video container
                                    merge_audio(video_path, wm_wav, out_vid)

                                    # 6. Read bytes before tmp dir closes
                                    with open(out_vid, "rb") as f:
                                        video_bytes = f.read()

                            # Show waveform so user can see the change
                            st.subheader("Waveform Comparison")
                            plot_waveform(samples, wm_samples, sr)

                            # ── STEP 2: serve the personalised file ──
                            st.download_button(
                                label="💾 Save your copy",
                                data=video_bytes,
                                file_name=row["filename"],
                                mime="video/mp4",
                                key=f"dl_save_{row['id']}"
                            )
                    else:
                        st.caption("File not found on disk")

            st.divider()


# ================================================================
#  TAB 3 — USERS
# ================================================================
with tabs[3]:
    st.header("👥 Registered Users")

    users_df = pd.read_sql_query(
        """
        SELECT  id       AS "User ID",
                username AS "Username",
                phone    AS "Phone",
                email    AS "Email"
        FROM    users
        """,
        conn
    )

    if users_df.empty:
        st.info("No registered users yet.")
    else:
        st.dataframe(users_df, use_container_width=True)


# ================================================================
#  TAB 4 — LOGOUT
# ================================================================
with tabs[4]:
    st.markdown("### Ready to leave?")
    st.markdown(f"Logged in as **{uname}** &nbsp;|&nbsp; User ID: `{uid}`")

    if st.button("Logout"):
        st.session_state.user_id  = None
        st.session_state.username = None
        st.rerun()
