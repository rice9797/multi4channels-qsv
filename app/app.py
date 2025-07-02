import os
import signal
import subprocess
import threading
import time
import requests
import json
from flask import Flask, request, render_template, jsonify
import re

app = Flask(__name__)
print("*** app.py started")

# Environment variables
CDVR_HOST = os.getenv("CDVR_HOST", "192.168.1.152")
CDVR_PORT = int(os.getenv("CDVR_PORT", "8089"))
CDVR_CHNLNUM = os.getenv("CDVR_CHNLNUM", "240")
RTP_HOST = os.getenv("RTP_HOST", "192.168.1.152")
RTP_PORT = str(os.getenv("RTP_PORT", "4444"))
OUTPUT_FPS = float(os.getenv("OUTPUT_FPS", "60"))
WEB_PAGE_PORT = int(os.getenv("WEB_PAGE_PORT", "9799"))
CHECK_INTERVAL_SECONDS = 60
KILL_COUNTDOWN_MINUTES = 6
CHANNELS = []
FAVORITES = []
FAVORITES_FILE = "/app/data/favorites.json"
STREAM_PROCESS = None
CURRENT_PID = None

# Mosaic settings
TARGET_WIDTH = 1920
TARGET_HEIGHT = 1080
BITRATE = "8000k"  # Suitable for 1080p 2x2 mosaic

def load_favorites():
    """Load favorite channels from JSON file."""
    global FAVORITES
    try:
        if os.path.exists(FAVORITES_FILE):
            with open(FAVORITES_FILE, 'r') as f:
                FAVORITES = json.load(f)
            print(f"*** Favorites loaded: {len(FAVORITES)} from {FAVORITES_FILE}")
        else:
            print(f"*** No favorites file at {FAVORITES_FILE}")
    except Exception as e:
        print(f"*** Error loading favorites: {e}")

def save_favorites():
    """Save favorite channels to JSON file."""
    try:
        with open(FAVORITES_FILE, 'w') as f:
            json.dump(FAVORITES, f, indent=2)
        print(f"*** Favorites saved: {len(FAVORITES)} to {FAVORITES_FILE}")
    except Exception as e:
        print(f"*** Error saving favorites: {e}")

load_favorites()

def scrape_m3u():
    """Scrape channel list from Channels DVR M3U."""
    global CHANNELS
    try:
        m3u_url = f"http://{CDVR_HOST}:{CDVR_PORT}/devices/ANY/channels.m3u"
        response = requests.get(m3u_url, timeout=5)
        if response.status_code == 200:
            lines = response.text.splitlines()
            channels = []
            current_channel = {}
            for line in lines:
                line = line.strip()
                if line.startswith('#EXTINF:'):
                    tvg_chno_match = re.search(r'tvg-chno="([^"]+)"', line)
                    tvg_name_match = re.search(r'tvg-name="([^"]+)"', line)
                    name_match = re.search(r',([^,]+)$', line)
                    if tvg_chno_match:
                        current_channel['number'] = tvg_chno_match.group(1)
                    if tvg_name_match:
                        current_channel['name'] = tvg_name_match.group(1)
                    elif name_match:
                        current_channel['name'] = name_match.group(1).strip()
                elif line.startswith('http://'):
                    if current_channel.get('number') and current_channel.get('name'):
                        channels.append({
                            'number': current_channel['number'],
                            'name': current_channel['name']
                        })
                    current_channel = {}
            CHANNELS = channels
            print(f"*** Channels loaded: {len(CHANNELS)} from M3U")
        else:
            print(f"*** Failed to fetch M3U: Status {response.status_code}")
    except Exception as e:
        print(f"*** Error scraping M3U: {e}")

scrape_m3u()

def detect_qsv():
    """Detect if Intel QuickSync Video (QSV) is available."""
    try:
        if not os.path.exists("/dev/dri"):
            print("*** No /dev/dri found, QSV unavailable")
            return False
        result = subprocess.run(["vainfo"], capture_output=True, text=True, check=True)
        if result.returncode == 0 and "VAEntrypointEncSlice" in result.stdout and "H.264" in result.stdout:
            print("*** Intel QuickSync H.264 encoding detected")
            print(f"*** vainfo output: {result.stdout}")
            return True
        print("*** vainfo failed or no QSV H.264 support")
        print(f"*** vainfo output: {result.stdout if result.stdout else 'No output'}")
        return False
    except subprocess.CalledProcessError as e:
        print(f"*** Error running vainfo: {e}")
        print(f"*** vainfo stderr: {e.stderr}")
        return False
    except Exception as e:
        print(f"*** Error detecting QSV: {e}")
        return False

VIDEO_CODEC = "h264_qsv" if detect_qsv() else "libx264"
print(f"*** Using video codec: {VIDEO_CODEC}")

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/start", methods=["POST"])
def start_stream():
    global STREAM_PROCESS, CURRENT_PID

    ch1 = request.form.get("ch1")
    ch2 = request.form.get("ch2")
    ch3 = request.form.get("ch3")
    ch4 = request.form.get("ch4")
    channels = [ch for ch in [ch1, ch2, ch3, ch4] if ch]

    print(f"*** Starting stream with channels: {', '.join(channels)}")

    if CURRENT_PID and STREAM_PROCESS:
        try:
            print(f"*** Terminating FFmpeg process PID {CURRENT_PID}")
            os.kill(CURRENT_PID, signal.SIGTERM)
            STREAM_PROCESS.wait(timeout=5)
            print(f"*** FFmpeg process PID {CURRENT_PID} terminated")
        except ProcessLookupError:
            print("*** Previous FFmpeg process already terminated")
        except subprocess.TimeoutExpired:
            print(f"*** FFmpeg process PID {CURRENT_PID} did not terminate gracefully, forcing kill")
            os.kill(CURRENT_PID, signal.SIGKILL)
            STREAM_PROCESS.wait(timeout=2)
        except Exception as e:
            print(f"*** Error terminating FFmpeg process: {e}")
        CURRENT_PID = None
        STREAM_PROCESS = None
        time.sleep(1)

    urls = [f"http://{CDVR_HOST}:{CDVR_PORT}/play/tuner/{ch}" for ch in channels]
    num_inputs = len(urls)

    if num_inputs == 0:
        return "No channels provided", 400

    ffmpeg_cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", "/app/photos/bg.jpg"
    ]

    # Add input streams
    for url in urls:
        ffmpeg_cmd += ["-i", url]

    # Build filter complex
    filter_parts = [
        f"[0:v]fps={OUTPUT_FPS},scale={TARGET_WIDTH}:{TARGET_HEIGHT},setsar=1[v0]"
    ]
    for i in range(num_inputs):
        filter_parts.append(
            f"[{i+1}:v]fps={OUTPUT_FPS},scale={TARGET_WIDTH//2}:{TARGET_HEIGHT//2},setsar=1[v{i+1}]"
        )

    # Mosaic layout
    positions = [
        (0, 0),                    # Top-left
        (TARGET_WIDTH//2, 0),      # Top-right
        (0, TARGET_HEIGHT//2),     # Bottom-left
        (TARGET_WIDTH//2, TARGET_HEIGHT//2)  # Bottom-right
    ]
    mosaic_parts = []
    for i in range(num_inputs):
        x, y = positions[i]
        mosaic_parts.append(f"[v{i+1}]overlay={x}:{y}")
    if mosaic_parts:
        filter_parts.append(f"[v0]{''.join(mosaic_parts)}[v]")

    filter_complex = ";".join(filter_parts) if num_inputs > 0 else "[0:v]copy[v]"

    ffmpeg_cmd += [
        "-filter_complex", filter_complex,
        "-map", "[v]"
    ]

    # Map audio tracks with metadata
    for i, ch in enumerate(channels):
        ffmpeg_cmd += [
            "-map", f"{i+1}:a",
            "-metadata:s:a:%d" % i, f"title=Ch {ch} Audio"
        ]

    # Encoding settings
    encoding_params = [
        "-c:v", VIDEO_CODEC,
        "-b:v", BITRATE,
        "-preset", "fast" if VIDEO_CODEC == "libx264" else "medium",
        "-c:a", "aac",
        "-b:a", "128k",
        "-f", "mpegts",
        f"udp://{RTP_HOST}:{RTP_PORT}?ttl=10"
    ]
    if VIDEO_CODEC == "h264_qsv":
        encoding_params += ["-vf", "hwupload=extra_hw_frames=64,format=qsv"]
    ffmpeg_cmd += encoding_params

    try:
        STREAM_PROCESS = subprocess.Popen(ffmpeg_cmd, stderr=subprocess.PIPE)
        CURRENT_PID = STREAM_PROCESS.pid
        print(f"*** FFmpeg started with PID {CURRENT_PID}")
    except Exception as e:
        print(f"*** Error starting FFmpeg process: {e}")
        return "Failed to start stream", 500

    if CDVR_CHNLNUM:
        threading.Thread(target=watch_for_quit, daemon=True).start()

    return "Stream started"

@app.route("/end", methods=["POST"])
def stop_stream():
    global STREAM_PROCESS, CURRENT_PID

    if CURRENT_PID and STREAM_PROCESS:
        try:
            print(f"*** Stopping FFmpeg process PID {CURRENT_PID}")
            os.kill(CURRENT_PID, signal.SIGTERM)
            STREAM_PROCESS.wait(timeout=5)
            print(f"*** FFmpeg process PID {CURRENT_PID} stopped")
        except ProcessLookupError:
            print("*** FFmpeg process already stopped")
        except subprocess.TimeoutExpired:
            print(f"*** FFmpeg process PID {CURRENT_PID} did not stop gracefully, forcing kill")
            os.kill(CURRENT_PID, signal.SIGKILL)
            STREAM_PROCESS.wait(timeout=2)
        except Exception as e:
            print(f"*** Error stopping FFmpeg process: {e}")
        CURRENT_PID = None
        STREAM_PROCESS = None
        return jsonify({"message": "Stream closed successfully"})
    else:
        return jsonify({"message": "No stream is running"})

@app.route("/reload")
def reload_m3u():
    scrape_m3u()
    return jsonify({"message": "M3U playlist reloaded successfully"})

@app.route("/channels")
def get_channels():
    return jsonify({"channels": CHANNELS, "favorites": FAVORITES})

@app.route("/toggle_favorite", methods=["POST"])
def toggle_favorite():
    global FAVORITES
    data = request.get_json()
    channel = {"number": data["number"], "name": data["name"]}
    if channel in FAVORITES:
        FAVORITES.remove(channel)
    else:
        FAVORITES.append(channel)
    save_favorites()
    return jsonify({"favorites": FAVORITES})

@app.route("/save_favorites", methods=["GET"])
def save_favorites_endpoint():
    save_favorites()
    return jsonify({"message": "Favorites saved successfully"})

def watch_for_quit():
    global CURRENT_PID
    inactive_minutes = 0
    print(f"*** Monitoring activity on channel {CDVR_CHNLNUM}")

    while True:
        try:
            r = requests.get(f"http://{CDVR_HOST}:{CDVR_PORT}/dvr", timeout=5)
            if r.status_code == 200:
                if f"ch{CDVR_CHNLNUM}".lower() in r.text.lower():
                    print(f"*** Channel {CDVR_CHNLNUM} still being watched")
                    inactive_minutes = 0
                else:
                    inactive_minutes += 1
                    print(f"*** Channel no longer being watched. Countdown to kill: {inactive_minutes} / {KILL_COUNTDOWN_MINUTES} min")
                    if inactive_minutes >= KILL_COUNTDOWN_MINUTES:
                        if CURRENT_PID:
                            try:
                                os.kill(CURRENT_PID, signal.SIGKILL)
                                print(f"*** Killed FFmpeg process PID {CURRENT_PID}")
                            except Exception as e:
                                print(f"*** Error killing FFmpeg: {e}")
                            CURRENT_PID = None
                        return
        except Exception as e:
            print(f"*** Error checking DVR activity: {e}")
        time.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    print("*** Starting Flask app")
    app.run(host="0.0.0.0", port=WEB_PAGE_PORT)
