import cv2
from ultralytics import YOLO
import pyttsx3
import threading
import queue
import time

# --- 1. The Background Speech Worker ---
speech_queue = queue.Queue()

def speech_worker():
    # Try using native Windows SAPI first (most stable in background threads on Windows)
    try:
        import win32com.client
        import pythoncom
        pythoncom.CoInitialize()
        speaker = win32com.client.Dispatch("SAPI.SpVoice")
        print("[AUDIO THREAD]: Using native Windows SAPI (SpVoice)")
        while True:
            text = speech_queue.get()
            if text is None: break
            try:
                print(f"[AUDIO THREAD]: Attempting to say '{text.upper()}'")
                speaker.Speak(text)
            except Exception as e:
                print(f"[AUDIO ERROR]: {e}")
            speech_queue.task_done()
        pythoncom.CoUninitialize()
        return
    except Exception as init_err:
        print(f"[AUDIO THREAD]: SAPI initialization failed, falling back to pyttsx3. Error: {init_err}")

    # Fallback to pyttsx3 (for non-Windows or if SAPI fails)
    try:
        import pythoncom
        pythoncom.CoInitialize()
    except Exception:
        pass
        
    engine = pyttsx3.init()
    engine.setProperty('rate', 150)
    while True:
        text = speech_queue.get()
        if text is None: break
        try:
            print(f"[AUDIO THREAD]: Attempting to say '{text.upper()}'")
            engine.say(text)
            engine.runAndWait()
        except Exception as e:
            print(f"[AUDIO ERROR]: {e}")
        speech_queue.task_done()

threading.Thread(target=speech_worker, daemon=True).start()

# --- 2. Load Model & Camera ---
model = YOLO("best.pt")
print("Scanning for available webcams...")
cap = None
# Try the first 4 camera slots one by one, using DirectShow first to bypass Windows MSMF bugs/blocking
for i in range(4):
    print(f"Trying camera index {i} with DirectShow...")
    temp_cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
    if temp_cap.isOpened():
        success, _ = temp_cap.read()
        if success:
            cap = temp_cap
            print(f"✅ Success! Connected to camera port {i} using DirectShow")
            break
    temp_cap.release()

    print(f"Trying camera index {i} with default backend...")
    temp_cap = cv2.VideoCapture(i)
    if temp_cap.isOpened():
        success, _ = temp_cap.read()
        if success:
            cap = temp_cap
            print(f"✅ Success! Connected to camera port {i} using default backend")
            break
    temp_cap.release()

if cap is None:
    print("🛑 FATAL ERROR: Windows is completely blocking your webcam.")
    print("Please check your privacy settings or unplug/replug the camera.")
    exit()

print("Starting webcam... Press 'q' to quit.")

# --- 3. Speech Tracking & Smoothing Variables ---
last_spoken_word = ""
last_spoken_time = 0
cooldown_seconds = 5.0 

# Smoothing parameters to prevent flickering and random detections
required_consecutive_frames = 8  # Sign must be detected for 8 consecutive frames (~0.3s) to trigger
required_empty_frames = 10       # No sign detected for 10 frames (~0.4s) will reset state

consecutive_detections = 0
current_candidate = ""
consecutive_empty_frames = 0

while cap.isOpened():
    success, frame = cap.read()
    
    # FIXED: Cleaned up the nested 'if' statement
    if not success: 
        print("\n🛑 ERROR: The webcam suddenly disconnected or stopped sending video!")
        break

    # Mirror the camera
    frame = cv2.flip(frame, 1)
    height, width, _ = frame.shape
    
    # --- 4. The Blue Guide Box (ROI) ---
    x1, y1 = width - 350, 100
    x2, y2 = width - 50, 400

    cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 0, 0), 2)
    cv2.putText(frame, "Put Hand Here", (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 0), 2)

    # Crop frame
    roi_frame = frame[y1:y2, x1:x2]
    
    # Set conf=0.20 to capture lower-confidence detections for display and debug printing
    results = model(roi_frame, conf=0.20, verbose=False)

    # Print raw detections to the console for debugging
    raw_dets = []
    for box in results[0].boxes:
        cls_id = int(box.cls[0].item())
        c_score = float(box.conf[0].item())
        name = results[0].names[cls_id]
        raw_dets.append(f"{name.upper()} ({c_score:.2f})")
    
    if raw_dets:
        print(f"\r[YOLO Raw]: {', '.join(raw_dets)}                     ", end="", flush=True)

    # --- 5. The Audio & Terminal Logic (with Debouncing and Reset) ---
    current_time = time.time()
    
    # Filter boxes that meet our confidence threshold for speech (e.g. 0.50)
    speech_boxes = [box for box in results[0].boxes if float(box.conf[0].item()) >= 0.50]
    
    if len(speech_boxes) > 0:
        # A sign is detected with confidence >= 0.50
        class_id = int(speech_boxes[0].cls[0].item())
        detected_word = results[0].names[class_id]
        
        consecutive_empty_frames = 0
        
        if detected_word == current_candidate:
            consecutive_detections += 1
        else:
            current_candidate = detected_word
            consecutive_detections = 1
            
        # Trigger speech if detection is stable (has persisted for enough frames)
        if consecutive_detections >= required_consecutive_frames:
            if current_candidate != last_spoken_word:
                print(f"\n[DETECTED]: {current_candidate.upper()}")
                speech_queue.put(current_candidate)
                last_spoken_word = current_candidate
                last_spoken_time = current_time
            elif (current_time - last_spoken_time) >= cooldown_seconds:
                print(f"\n[REPEAT]: {current_candidate.upper()}")
                speech_queue.put(current_candidate)
                last_spoken_time = current_time
    else:
        # No sign detected
        consecutive_empty_frames += 1
        consecutive_detections = 0
        current_candidate = ""
        
        # Reset detection state if hand is removed for a short while
        if consecutive_empty_frames >= required_empty_frames:
            if last_spoken_word != "":
                print("\n[INFO]: Hand removed, resetting detection state.")
                last_spoken_word = ""

    annotated_roi = results[0].plot()
    frame[y1:y2, x1:x2] = annotated_roi
    
    cv2.imshow("Marathi Sign Language", frame)

    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

cap.release()
cv2.destroyAllWindows()