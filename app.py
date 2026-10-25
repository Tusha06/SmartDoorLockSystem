from flask import Flask, render_template, Response, jsonify, request, send_from_directory
import cv2
import face_recognition
import numpy as np
import os
import requests
from datetime import datetime
import threading
import time

app = Flask(__name__)

# ===== CONFIGURATION & CREDENTIALS =====
TELEGRAM_TOKEN    = "8975226283:AAHvIK8BnZb7X1Bzov2EVnnZGBv71QyL5aI"
CHAT_ID           = "1748309903"
KNOWN_FACES_DIR   = "known_faces"
BLYNK_AUTH_TOKEN  = "jaom-iUT1r94Dv-_6AT0mc5awAAfG_vS"
BLYNK_PIN         = "V1"  # Virtual Pin for door lock (0 = Unlocked, 1 = Locked)
AUTO_RELOCK_DELAY = 5     # Door auto re-locks after 5 seconds
UNLOCK_COOLDOWN   = 15    # Cooldown before next auto-unlock trigger (seconds)
COOLDOWN          = 15    # Telegram alert cooldown for unknown faces (seconds)
# =======================================

# Load authorized/known face encodings from disk
known_encodings = []
known_names     = []

print("Loading known faces...")
# Ensure the known_faces directory exists
if not os.path.exists(KNOWN_FACES_DIR):
    os.makedirs(KNOWN_FACES_DIR)

for filename in os.listdir(KNOWN_FACES_DIR):
    if filename.endswith((".jpg", ".png", ".jpeg")):
        img  = face_recognition.load_image_file(f"{KNOWN_FACES_DIR}/{filename}")
        encs = face_recognition.face_encodings(img)
        if encs:
            known_encodings.append(encs[0])
            known_names.append(os.path.splitext(filename)[0])
            print(f"Loaded: {filename}")

print(f"Total known faces: {len(known_names)}")

# Camera & Lock State Management
camera       = cv2.VideoCapture(0)
door_locked  = True
last_alert   = 0
last_unlock  = 0
is_unlocking = False

def send_telegram_photo(image, message):
    """Sends a captured image to Telegram with a text caption"""
    try:
        _, buffer = cv2.imencode('.jpg', image)
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
        requests.post(url, data={"chat_id": CHAT_ID, "caption": message},
                      files={"photo": ("photo.jpg", buffer.tobytes(), "image/jpeg")},
                      timeout=10)
        print("Telegram photo alert sent!")
    except Exception as e:
        print(f"Telegram photo error: {e}")

def send_telegram_message(message):
    """Sends a plain text alert message to Telegram"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": message}, timeout=10)
        print("Telegram text alert sent!")
    except Exception as e:
        print(f"Telegram text error: {e}")

def trigger_auto_unlock(person_name, frame=None):
    """Unlocks the door via the Blynk Cloud API and automatically re-locks after a delay"""
    global door_locked, is_unlocking
    if is_unlocking:
        return
    is_unlocking = True
    try:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Recognized {person_name}! Sending UNLOCK command to Blynk...")
        # 1. Send unlock command to Blynk (0 = Unlocked)
        url_unlock = f"https://blynk.cloud/external/api/update?token={BLYNK_AUTH_TOKEN}&{BLYNK_PIN}=0"
        requests.get(url_unlock, timeout=5)
        door_locked = False
        print("Door UNLOCKED successfully!")

        # Send Telegram notification
        msg = f"Known face recognized: {person_name}!\nDoor UNLOCKED automatically.\nTime: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        if frame is not None:
            threading.Thread(target=send_telegram_photo, args=(frame, msg), daemon=True).start()
        else:
            threading.Thread(target=send_telegram_message, args=(msg,), daemon=True).start()

        # 2. Keep door unlocked for preset delay
        time.sleep(AUTO_RELOCK_DELAY)

        # 3. Send lock command to Blynk (1 = Locked)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Auto re-locking door...")
        url_lock = f"https://blynk.cloud/external/api/update?token={BLYNK_AUTH_TOKEN}&{BLYNK_PIN}=1"
        requests.get(url_lock, timeout=5)
        door_locked = True
        print("Door LOCKED securely!")
    except Exception as e:
        print(f"Error during auto door unlock: {e}")
    finally:
        is_unlocking = False

def process_frame(frame):
    """Processes video frame for face detection, recognition, and auto-unlock triggers"""
    global last_alert, last_unlock, is_unlocking

    # Resize frame for faster processing
    small = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)
    rgb   = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)

    locations  = face_recognition.face_locations(rgb)
    encodings  = face_recognition.face_encodings(rgb, locations)

    for (top, right, bottom, left), enc in zip(locations, encodings):
        # Scale back up to original frame dimensions
        top    *= 4
        right  *= 4
        bottom *= 4
        left   *= 4

        matches = face_recognition.compare_faces(known_encodings, enc, tolerance=0.5)
        name    = "Unknown"

        if True in matches:
            idx  = np.argmin(face_recognition.face_distance(known_encodings, enc))
            name = known_names[idx]

        # Draw bounding box and name tag
        color = (0, 255, 0) if name != "Unknown" else (0, 0, 255)
        cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
        cv2.putText(frame, name, (left, top - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)

        # Alert & Auto-Unlock logic
        now = time.time()
        if name != "Unknown":
            # Authorized person recognized -> Trigger automatic unlock
            if (now - last_unlock) > UNLOCK_COOLDOWN and not is_unlocking:
                last_unlock = now
                threading.Thread(target=trigger_auto_unlock, args=(name, frame.copy()), daemon=True).start()
        else:
            # Unknown visitor detected -> Send Telegram alert with photo if door is locked
            if door_locked and (now - last_alert) > COOLDOWN:
                last_alert = now
                msg = f"Unknown person detected at door!\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                threading.Thread(target=send_telegram_photo, args=(frame.copy(), msg), daemon=True).start()

    return frame

def generate_frames():
    """Generates MJPEG video stream frames for the web dashboard"""
    while True:
        success, frame = camera.read()
        if not success:
            break

        frame = process_frame(frame)

        _, buffer = cv2.imencode('.jpg', frame)
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' +
               buffer.tobytes() + b'\r\n')

# ===== HTTP ROUTES =====

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/remote')
def remote():
    """Serves the standalone Remote Lock Web application"""
    return send_from_directory('RemoteLockWeb', 'index.html')

@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/pir_trigger', methods=['POST'])
def pir_trigger():
    """Handles PIR motion sensor trigger requests sent from ESP32"""
    global last_alert
    now = time.time()
    if door_locked and (now - last_alert) > COOLDOWN:
        last_alert = now
        success, frame = camera.read()
        if success:
            frame = process_frame(frame)
            msg = f"Motion detected! Door is locked.\n{datetime.now().strftime('%H:%M:%S')}"
            threading.Thread(target=send_telegram_photo, args=(frame.copy(), msg), daemon=True).start()
    return jsonify({"status": "ok"})

@app.route('/door_status', methods=['POST'])
def door_status():
    """Handles door lock status synchronization updates sent from ESP32"""
    global door_locked
    data = request.get_json(silent=True) or {}
    door_locked = data.get('locked', True)
    print(f"Door status: {'Locked' if door_locked else 'Unlocked'}")
    return jsonify({"status": "ok"})

@app.route('/unlock_door', methods=['POST'])
def unlock_door():
    """Triggers manual door unlock from the web dashboard"""
    threading.Thread(target=trigger_auto_unlock, args=("Web Dashboard",), daemon=True).start()
    return jsonify({"status": "unlocking"})

@app.route('/get_status')
def get_status():
    return jsonify({
        "door_locked": door_locked,
        "is_unlocking": is_unlocking,
        "known_faces": known_names
    })

if __name__ == '__main__':
    print("Server starting on http://0.0.0.0:5000")
    app.run(host='0.0.0.0', port=5000, debug=False)
