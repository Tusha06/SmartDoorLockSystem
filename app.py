from flask import Flask, render_template, Response, jsonify
import cv2
import face_recognition
import numpy as np
import os
import requests
from datetime import datetime
import threading

app = Flask(__name__)

# ===== TUMCHI DETAILS ITHEY BHARA =====
TELEGRAM_TOKEN = "8975226283:AAHvIK8BnZb7X1Bzov2EVnnZGBv71QyL5aI"
CHAT_ID        = "1748309903"
KNOWN_FACES_DIR = "known_faces"
# =======================================

# Known faces load karo
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

# Webcam
camera     = cv2.VideoCapture(0)
door_locked = True
last_alert  = 0
COOLDOWN    = 15  # seconds

def send_telegram_photo(image, message):
    """Telegram var photo pathvto"""
    try:
        _, buffer = cv2.imencode('.jpg', image)
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
        requests.post(url, data={"chat_id": CHAT_ID, "caption": message},
                      files={"photo": ("photo.jpg", buffer.tobytes(), "image/jpeg")})
        print("Telegram alert sent!")
    except Exception as e:
        print(f"Telegram error: {e}")

def process_frame(frame):
    """Face detection aani recognition"""
    global last_alert

    # Resize for faster processing
    small = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)
    rgb   = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)

    locations  = face_recognition.face_locations(rgb)
    encodings  = face_recognition.face_encodings(rgb, locations)

    for (top, right, bottom, left), enc in zip(locations, encodings):
        # Scale back up
        top    *= 4
        right  *= 4
        bottom *= 4
        left   *= 4

        matches = face_recognition.compare_faces(known_encodings, enc, tolerance=0.5)
        name    = "Unknown"

        if True in matches:
            idx  = np.argmin(face_recognition.face_distance(known_encodings, enc))
            name = known_names[idx]

        # Box draw karo
        color = (0, 255, 0) if name != "Unknown" else (0, 0, 255)
        cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
        cv2.putText(frame, name, (left, top - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)

        # Alert logic
        import time
        now = time.time()
        if door_locked and name == "Unknown" and (now - last_alert) > COOLDOWN:
            last_alert = now
            msg = f"Unknown person detected at door!\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            threading.Thread(target=send_telegram_photo, args=(frame.copy(), msg)).start()

    return frame

def generate_frames():
    """Video stream generate karto"""
    while True:
        success, frame = camera.read()
        if not success:
            break

        frame = process_frame(frame)

        _, buffer = cv2.imencode('.jpg', frame)
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' +
               buffer.tobytes() + b'\r\n')

# ===== ROUTES =====

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/pir_trigger', methods=['POST'])
def pir_trigger():
    """ESP32 ye call karto jab PIR trigger hoto"""
    global last_alert
    import time
    now = time.time()
    if door_locked and (now - last_alert) > COOLDOWN:
        last_alert = now
        success, frame = camera.read()
        if success:
            frame = process_frame(frame)
            msg = f"Motion detected! Door is locked.\n{datetime.now().strftime('%H:%M:%S')}"
            threading.Thread(target=send_telegram_photo, args=(frame.copy(), msg)).start()
    return jsonify({"status": "ok"})

@app.route('/door_status', methods=['POST'])
def door_status():
    """ESP32 door status update karto"""
    global door_locked
    from flask import request
    data = request.get_json()
    door_locked = data.get('locked', True)
    print(f"Door status: {'Locked' if door_locked else 'Unlocked'}")
    return jsonify({"status": "ok"})

@app.route('/get_status')
def get_status():
    return jsonify({
        "door_locked": door_locked,
        "known_faces": known_names
    })

if __name__ == '__main__':
    print("Server starting on http://0.0.0.0:5000")
    app.run(host='0.0.0.0', port=5000, debug=False)
