import os
import signal
import subprocess
import threading
import time
import requests
import json
from flask import Flask, Response, request, render_template_string, jsonify, stream_with_context
import re
import logging
import sys

app = Flask(__name__)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('/tmp/app.log')
    ]
)
logging.info("*** app.py started")

# Environment variables
CDVR_HOST = os.getenv("CDVR_HOST", "192.168.1.151")
CDVR_PORT = int(os.getenv("CDVR_PORT", "8089"))
CDVR_CHNLNUM = os.getenv("CDVR_CHNLNUM", "240")
WEB_PAGE_PORT = int(os.getenv("WEB_PAGE_PORT", "9799"))
STREAM_PORT = int(os.getenv("STREAM_PORT", "5444"))
CHECK_INTERVAL_SECONDS = 60
KILL_COUNTDOWN_MINUTES = 6
CHANNELS = []
FAVORITES = []
FAVORITES_FILE = "/app/data/favorites.json"
STREAM_PROCESS = None
CURRENT_PID = None

# Mosaic settings
TARGET_WIDTH = 1280
TARGET_HEIGHT = 720
TARGET_FPS = float(os.getenv("OUTPUT_FPS", "29.97"))
BITRATE = "5120k"

def load_favorites():
    """Load favorite channels from JSON file."""
    global FAVORITES
    try:
        with open(FAVORITES_FILE, 'r') as f:
            FAVORITES = json.load(f)
        logging.info("*** Favorites loaded: %d from %s", len(FAVORITES), FAVORITES_FILE)
    except Exception as e:
        logging.error("*** Error loading favorites: %s", str(e))
        FAVORITES = []

def save_favorites():
    """Save favorite channels to JSON file."""
    try:
        with open(FAVORITES_FILE, 'w') as f:
            json.dump(FAVORITES, f, indent=2)
        os.chmod(FAVORITES_FILE, 0o666)
        logging.info("*** Favorites saved: %d to %s", len(FAVORITES), FAVORITES_FILE)
    except Exception as e:
        logging.error("*** Error saving favorites: %s", str(e))

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
            logging.info("*** Channels loaded: %d from M3U", len(CHANNELS))
        else:
            logging.error("*** Failed to fetch M3U: Status %d", response.status_code)
    except Exception as e:
        logging.error("*** Error scraping M3U: %s", str(e))

scrape_m3u()

def detect_qsv():
    """Detect if Intel QuickSync Video (QSV) is available."""
    try:
        if not os.path.exists("/dev/dri"):
            logging.error("*** No /dev/dri found, QSV unavailable")
            return False
        result = subprocess.run(["vainfo"], capture_output=True, text=True, check=True)
        if result.returncode == 0 and "VAEntrypointEncSlice" in result.stdout and "H.264" in result.stdout:
            logging.info("*** Intel QuickSync H.264 encoding detected")
            logging.info("*** vainfo output: %s", result.stdout)
            return True
        logging.error("*** vainfo failed or no QSV H.264 support")
        logging.error("*** vainfo output: %s", result.stdout if result.stdout else "No output")
        return False
    except subprocess.CalledProcessError as e:
        logging.error("*** Error running vainfo: %s", str(e))
        logging.error("*** vainfo stderr: %s", e.stderr)
        return False
    except Exception as e:
        logging.error("*** Error detecting QSV: %s", str(e))
        return False

VIDEO_CODEC = "h264_qsv" if detect_qsv() else "libx264"
logging.info("*** Using video codec: %s", VIDEO_CODEC)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Multi4Channels</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {
            font-family: 'Arial', 'Helvetica', sans-serif;
            background: #111;
            color: white;
            margin: 0;
            overflow-x: hidden;
        }
        header {
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 0.5em 1em;
            background: #222;
            position: fixed;
            top: 0;
            width: 100%;
            z-index: 10;
        }
        h1 {
            font-size: 1.8em;
            margin: 0;
            text-align: center;
        }
        .hamburger {
            font-size: 1.8em;
            cursor: pointer;
            padding: 0.8em;
            position: absolute;
            right: 0.5em;
            text-overflow: clip;
            white-space: nowrap;
            z-index: 11;
        }
        #menu {
            position: fixed;
            top: 0;
            right: 0;
            width: 70%;
            max-width: 250px;
            height: 100%;
            background: #222;
            transform: translateX(100%);
            transition: transform 0.3s ease;
            padding-top: 4em;
            z-index: 9;
        }
        #menu.open {
            transform: translateX(0);
        }
        #menu ul {
            list-style: none;
            padding: 0;
            margin: 0;
        }
        #menu li {
            padding: 1em 1.5em;
            border-bottom: 1px solid #444;
            font-size: 1.1em;
            cursor: pointer;
        }
        #menu li:hover {
            background: #333;
        }
        .container {
            padding: 4.5em 1em 1em;
            max-width: 600px;
            margin: 0 auto;
        }
        form {
            text-align: center;
        }
        input[type=text] {
            font-size: 1.5em;
            width: 4.5em;
            margin: 0.3em;
            padding: 0.2em;
            box-sizing: border-box;
            border-radius: 4px;
        }
        .grid {
            display: flex;
            flex-wrap: wrap;
            justify-content: center;
            gap: 0.3em;
        }
        .grid div {
            width: 48%;
            min-width: 100px;
        }
        input[type=submit] {
            font-size: 1.1em;
            padding: 0.6em 1.2em;
            margin: 1em 0;
            cursor: pointer;
            background: #007bff;
            color: white;
            border: none;
            border-radius: 4px;
        }
        input[type=submit]:hover {
            background: #0056b3;
        }
        #favorites {
            margin-top: 1.5em;
            text-align: center;
        }
        #favorites h2 {
            font-size: 1.3em;
            margin: 0.5em 0;
        }
        #favorites ul {
            list-style: none;
            padding: 0;
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 0.5em;
        }
        #favorites li {
            font-size: 0.95em;
            padding: 0.6em;
            background: #333;
            margin: 0.3em;
            border-radius: 4px;
            cursor: pointer;
        }
        #favorites li:hover {
            background: #444;
        }
        #channels-page {
            display: none;
            padding: 4.5em 1em 1em;
            max-width: 600px;
            margin: 0 auto;
        }
        #channels-page h2 {
            font-size: 1.3em;
            margin: 0.5em 0;
        }
        #channels-page ul {
            list-style: none;
            padding: 0;
        }
        #channels-page li {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 0.6em;
            background: #333;
            margin: 0.3em 0;
            border-radius: 4px;
            font-size: 0.95em;
        }
        .heart {
            cursor: pointer;
            font-size: 1.2em;
            padding: 0.3em;
        }
        .heart.favorited {
            color: red;
        }
        .button-group {
            text-align: center;
            margin-top: 1em;
        }
        .button-group button.save {
            font-size: 1.1em;
            padding: 0.6em 1.2em;
            margin: 0.5em;
            cursor: pointer;
            background: #dc3545;
            color: white;
            border: none;
            border-radius: 4px;
        }
        .button-group button.save:hover {
            background: #c82333;
        }
        .button-group button.back {
            font-size: 1.1em;
            padding: 0.6em 1.2em;
            margin: 0.5em;
            cursor: pointer;
            background: #007bff;
            color: white;
            border: none;
            border-radius: 4px;
        }
        .button-group button.back:hover {
            background: #0056b3;
        }
        #notification {
            display: none;
            position: fixed;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            background: #333;
            padding: 1em 2em;
            border-radius: 8px;
            box-shadow: 0 0 10px rgba(0,0,0,0.5);
            z-index: 1000;
            text-align: center;
        }
        #notification p {
            font-size: 0.95em;
            margin: 0 0 1em;
        }
        #notification button {
            font-size: 1em;
            padding: 0.5em 1em;
            cursor: pointer;
            background: #007bff;
            color: white;
            border: none;
            border-radius: 4px;
        }
        #notification button:hover {
            background: #0056b3;
        }
        @media (min-width: 600px) {
            h1 {
                font-size: 2.2em;
            }
            input[type=text] {
                font-size: 1.8em;
            }
            input[type=submit] {
                font-size: 1.2em;
            }
            #menu li {
                font-size: 1.2em;
            }
            #favorites ul {
                gap: 1em;
            }
        }
        @media (max-width: 360px) {
            .grid div {
                width: 100%;
            }
            .hamburger {
                padding: 0.5em;
                right: 0.2em;
            }
        }
    </style>
</head>
<body>
    <header>
        <h1>Multi4Channels</h1>
        <div class="hamburger">☰</div>
    </header>
    <div id="menu">
        <ul>
            <li onclick="reloadM3U()">Reload M3U from Channels</li>
            <li onclick="showChannels()">Available Channels</li>
            <li onclick="closeStream()">Close Current Stream</li>
            <li onclick="goHome()">Home</li>
        </ul>
    </div>
    <div class="container">
        <form method="post" action="/start" id="stream-form">
            <div class="grid">
                <div><input name="ch1" type="text" placeholder="Ch1" inputmode="decimal"></div>
                <div><input name="ch2" type="text" placeholder="Ch2" inputmode="decimal"></div>
                <div><input name="ch3" type="text" placeholder="Ch3" inputmode="decimal"></div>
                <div><input name="ch4" type="text" placeholder="Ch4" inputmode="decimal"></div>
            </div>
            <input type="submit" value="Start Stream">
        </form>
        <div id="favorites">
            <h2>Favorites</h2>
            <ul id="favorites-list"></ul>
        </div>
    </div>
    <div id="channels-page">
        <h2>Available Channels</h2>
        <ul id="channels-list"></ul>
        <div class="button-group">
            <button class="save" onclick="saveFavorites()">Save</button>
            <button class="back" onclick="goHome()">Back</button>
        </div>
    </div>
    <div id="notification">
        <p id="notification-text">Stream started</p>
        <button onclick="dismissNotification()">Dismiss</button>
    </div>

    <script>
        const menu = document.getElementById('menu');
        const hamburger = document.querySelector('.hamburger');
        const channelsPage = document.getElementById('channels-page');
        const mainContainer = document.querySelector('.container');
        const notification = document.getElementById('notification');
        const notificationText = document.getElementById('notification-text');
        const streamForm = document.getElementById('stream-form');

        hamburger.addEventListener('click', () => {
            menu.classList.toggle('open');
        });

        document.addEventListener('click', e => {
            if (!menu.contains(e.target) && !hamburger.contains(e.target)) {
                menu.classList.remove('open');
            }
        });

        function reloadM3U() {
            fetch('/reload_m3u')
                .then(response => response.json())
                .then(data => {
                    alert(data.message);
                    menu.classList.remove('open');
                });
        }

        function showChannels() {
            fetch('/channels')
                .then(response => response.json())
                .then(data => {
                    const channelsList = document.getElementById('channels-list');
                    channelsList.innerHTML = '';
                    data.channels.forEach(channel => {
                        const li = document.createElement('li');
                        const isFavorited = data.favorites.some(fav => fav.number === channel.number);
                        li.innerHTML = `
                            ${channel.name} (${channel.number})
                            <span class="heart ${isFavorited ? 'favorited' : ''}" data-number="${channel.number}" data-name="${channel.name}">${isFavorited ? '♥' : '♡'}</span>
                        `;
                        channelsList.appendChild(li);
                    });
                    mainContainer.style.display = 'none';
                    channelsPage.style.display = 'block';
                    menu.classList.remove('open');
                    attachHeartListeners();
                });
        }

        function closeStream() {
            fetch('/stop', { method: 'POST' })
                .then(response => response.json())
                .then(data => {
                    notificationText.textContent = data.message;
                    notification.style.display = 'block';
                    setTimeout(() => {
                        notification.style.display = 'none';
                    }, 3000);
                    menu.classList.remove('open');
                });
        }

        function goHome() {
            channelsPage.style.display = 'none';
            mainContainer.style.display = 'block';
            menu.classList.remove('open');
        }

        function saveFavorites() {
            fetch('/save_favorites')
                .then(response => response.json())
                .then(data => {
                    alert(data.message);
                });
        }

        function dismissNotification() {
            notification.style.display = 'none';
        }

        function attachHeartListeners() {
            document.querySelectorAll('.heart').forEach(heart => {
                heart.addEventListener('click', () => {
                    const number = heart.dataset.number;
                    const name = heart.dataset.name;
                    fetch('/toggle_favorite', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ number, name })
                    })
                    .then(response => response.json())
                    .then(data => {
                        heart.classList.toggle('favorited');
                        heart.textContent = heart.classList.contains('favorited') ? '♥' : '♡';
                        updateFavoritesList(data.favorites);
                    });
                });
            });
        }

        function updateFavoritesList(favorites) {
            const favoritesList = document.getElementById('favorites-list');
            favoritesList.innerHTML = '';
            favorites.forEach(fav => {
                const li = document.createElement('li');
                li.textContent = `${fav.name} (${fav.number})`;
                li.dataset.number = fav.number;
                li.addEventListener('click', () => {
                    const inputs = document.querySelectorAll('input[name^="ch"]');
                    for (let input of inputs) {
                        if (!input.value) {
                            input.value = fav.number;
                            break;
                        }
                    }
                });
                favoritesList.appendChild(li);
            });
        }

        fetch('/channels')
            .then(response => response.json())
            .then(data => updateFavoritesList(data.favorites));

        streamForm.addEventListener('submit', e => {
            e.preventDefault();
            const formData = new FormData(streamForm);
            const channels = [];
            formData.forEach((value, key) => {
                if (value) channels.push(value);
            });
            const url = '/combine?' + channels.map(ch => `ch=${ch}`).join('&');
            fetch(url)
                .then(response => {
                    if (response.ok) {
                        notificationText.textContent = 'Stream started';
                        notification.style.display = 'block';
                        setTimeout(() => {
                            notification.style.display = 'none';
                        }, 5000);
                    } else {
                        notificationText.textContent = 'Failed to start stream';
                        notification.style.display = 'block';
                        setTimeout(() => {
                            notification.style.display = 'none';
                        }, 5000);
                    }
                });
        });
    </script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route("/start", methods=["POST"])
def start_stream():
    ch1 = request.form.get("ch1")
    ch2 = request.form.get("ch2")
    ch3 = request.form.get("ch3")
    ch4 = request.form.get("ch4")
    channels = [ch for ch in [ch1, ch2, ch3, ch4] if ch]
    if not channels:
        return "No channels provided", 400
    query = "&".join(f"ch={ch}" for ch in channels)
    return jsonify({"message": f"Stream started, access at /combine?{query}"})

@app.route("/combine")
def combine_streams():
    global STREAM_PROCESS, CURRENT_PID
    channels = request.args.getlist('ch')[:4]
    if not channels:
        return "No channels provided", 400

    # Terminate existing stream
    if CURRENT_PID and STREAM_PROCESS:
        try:
            logging.info("*** Terminating FFmpeg process PID %d", CURRENT_PID)
            STREAM_PROCESS.terminate()
            STREAM_PROCESS.wait(timeout=5)
            logging.info("*** FFmpeg process PID %d terminated", CURRENT_PID)
        except ProcessLookupError:
            logging.info("*** Previous FFmpeg process already terminated")
        except subprocess.TimeoutExpired:
            logging.warning("*** FFmpeg process PID %d did not terminate gracefully, forcing kill", CURRENT_PID)
            STREAM_PROCESS.kill()
            STREAM_PROCESS.wait(timeout=2)
        except Exception as e:
            logging.error("*** Error terminating FFmpeg process: %s", str(e))
        CURRENT_PID = None
        STREAM_PROCESS = None

    urls = [f"http://{CDVR_HOST}:{CDVR_PORT}/devices/ANY/channels/{ch}/stream.mpg" for ch in channels]
    num_inputs = len(urls)

    ffmpeg_cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error']

    # Add input URLs
    for url in urls:
        ffmpeg_cmd += ['-i', url]

    # Build scaling filters
    filter_parts = [
        f'[{i}:v]fps={TARGET_FPS},scale={TARGET_WIDTH//2}:{TARGET_HEIGHT//2},setsar=1[v{i}]' for i in range(num_inputs)
    ]

    # Build xstack layout
    layout_map = {
        1: "[v0]xstack=inputs=1:layout=0_0[v]",
        2: "[v0][v1]xstack=inputs=2:layout=0_0|w0_0[v]",
        3: "[v0][v1][v2]xstack=inputs=3:layout=0_0|w0_0|0_h0[v]",
        4: "[v0][v1][v2][v3]xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0[v]"
    }
    filter_parts.append(layout_map[num_inputs])

    filter_complex = ';'.join(filter_parts)

    ffmpeg_cmd += ['-filter_complex', filter_complex, '-map', '[v]']

    # Map all audio tracks individually
    for i, ch in enumerate(channels):
        ffmpeg_cmd += [
            '-map', f'{i}:a',
            '-metadata:s:a:%d' % i, f'title=Ch {ch} Audio'
        ]

    # Encoding settings
    encoding_params = [
        '-c:v', VIDEO_CODEC,
        '-b:v', BITRATE,
        '-preset', 'fast' if VIDEO_CODEC == 'libx264' else 'medium',
        '-c:a', 'aac',
        '-b:a', '128k',
        '-f', 'mpegts',
        'pipe:1'
    ]
    if VIDEO_CODEC == 'h264_qsv':
        encoding_params += ['-vf', 'hwupload=extra_hw_frames=64,format=qsv']
    ffmpeg_cmd += encoding_params

    def generate():
        global STREAM_PROCESS, CURRENT_PID
        STREAM_PROCESS = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        CURRENT_PID = STREAM_PROCESS.pid
        logging.info("*** FFmpeg started with PID %d for channels: %s", CURRENT_PID, ', '.join(channels))
        try:
            while True:
                chunk = STREAM_PROCESS.stdout.read(1024 * 16)
                if not chunk:
                    break
                yield chunk
        except Exception as e:
            logging.error("*** Error streaming: %s", str(e))
        finally:
            if STREAM_PROCESS:
                STREAM_PROCESS.terminate()
                try:
                    STREAM_PROCESS.wait(timeout=5)
                    logging.info("*** FFmpeg process PID %d terminated", CURRENT_PID)
                except subprocess.TimeoutExpired:
                    STREAM_PROCESS.kill()
                    logging.warning("*** FFmpeg process PID %d killed", CURRENT_PID)
                CURRENT_PID = None
                STREAM_PROCESS = None

    # Start monitoring for Channels DVR activity
    if CDVR_CHNLNUM:
        threading.Thread(target=watch_for_quit, daemon=True).start()

    return Response(stream_with_context(generate()), mimetype='video/MP2T')

@app.route("/stop", methods=["POST"])
def stop_stream():
    global STREAM_PROCESS, CURRENT_PID
    if CURRENT_PID and STREAM_PROCESS:
        try:
            logging.info("*** Stopping FFmpeg process PID %d", CURRENT_PID)
            STREAM_PROCESS.terminate()
            STREAM_PROCESS.wait(timeout=5)
            logging.info("*** FFmpeg process PID %d stopped", CURRENT_PID)
        except ProcessLookupError:
            logging.info("*** FFmpeg process already stopped")
        except subprocess.TimeoutExpired:
            logging.warning("*** FFmpeg process PID %d did not stop gracefully, forcing kill", CURRENT_PID)
            STREAM_PROCESS.kill()
            STREAM_PROCESS.wait(timeout=2)
        except Exception as e:
            logging.error("*** Error stopping FFmpeg process: %s", str(e))
        finally:
            CURRENT_PID = None
            STREAM_PROCESS = None
            return jsonify({"message": "Stream closed successfully"})
    else:
        return jsonify({"message": "No stream is running"})

@app.route("/reload_m3u")
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

@app.route("/save_favorites")
def save_favorites_endpoint():
    save_favorites()
    return jsonify({"message": "Favorites saved successfully"})

def watch_for_quit():
    global CURRENT_PID, STREAM_PROCESS
    inactive_minutes = 0
    logging.info("*** Monitoring activity on channel %s", CDVR_CHNLNUM)

    while True:
        try:
            r = requests.get(f"http://{CDVR_HOST}:{CDVR_PORT}/dvr", timeout=5)
            if r.status_code == 200:
                if f"ch{CDVR_CHNLNUM}".lower() in r.text.lower():
                    logging.info("*** Channel %s still being watched", CDVR_CHNLNUM)
                    inactive_minutes = 0
                else:
                    inactive_minutes += 1
                    logging.info("*** Channel no longer being watched. Countdown to kill: %d / %d min", inactive_minutes, KILL_COUNTDOWN_MINUTES)
                    if inactive_minutes >= KILL_COUNTDOWN_MINUTES:
                        if CURRENT_PID and STREAM_PROCESS:
                            try:
                                logging.info("*** Killing FFmpeg process PID %d", CURRENT_PID)
                                STREAM_PROCESS.terminate()
                                STREAM_PROCESS.wait(timeout=5)
                                logging.info("*** FFmpeg process PID %d killed", CURRENT_PID)
                            except Exception as e:
                                logging.error("*** Error killing FFmpeg: %s", str(e))
                            CURRENT_PID = None
                            STREAM_PROCESS = None
                        return
        except Exception as e:
            logging.error("*** Error checking DVR activity: %s", str(e))

        time.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    logging.info("*** Starting Flask app")
    app.run(host="0.0.0.0", port=WEB_PAGE_PORT)
