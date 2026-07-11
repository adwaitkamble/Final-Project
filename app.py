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
    
    # 1. OpenCV Contour & Skin Presence Verification (No external libraries required)
    ycrcb = cv2.cvtColor(roi_frame, cv2.COLOR_BGR2YCrCb)
    lower_skin = (0, 133, 77)
    upper_skin = (255, 173, 127)
    skin_mask = cv2.inRange(ycrcb, lower_skin, upper_skin)
    
    # Clean up noise using morphological transformations
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    skin_mask = cv2.morphologyEx(skin_mask, cv2.MORPH_OPEN, kernel, iterations=2)
    skin_mask = cv2.morphologyEx(skin_mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    
    # Find contours to check if a real, physical human hand is inside the box
    contours, _ = cv2.findContours(skin_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    has_hand = False
    hand_box = None
    if contours:
        largest_contour = max(contours, key=cv2.contourArea)
        max_area = cv2.contourArea(largest_contour)
        # A human hand placed in a 300x300 box forms a solid contour >= 4500 pixels
        if max_area >= 4500:
            has_hand = True
            hx, hy, hw, hh = cv2.boundingRect(largest_contour)
            hand_box = (hx, hy, hx + hw, hy + hh)
            cv2.rectangle(roi_frame, (hx, hy), (hx + hw, hy + hh), (0, 255, 0), 2)
            cv2.putText(roi_frame, "Hand Tracked", (hx, max(20, hy - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
            
    # Set conf=0.20 to capture lower-confidence detections for display and debug printing
    results = model(roi_frame, conf=0.20, verbose=False)

    # Analyze raw detections and verify overlap with tracked hand contour
    raw_dets = []
    speech_boxes = []
    
    for box in results[0].boxes:
        cls_id = int(box.cls[0].item())
        c_score = float(box.conf[0].item())
        name = results[0].names[cls_id]
        
        bx1, by1, bx2, by2 = map(int, box.xyxy[0].tolist())
        bx1 = max(0, min(roi_frame.shape[1], bx1))
        bx2 = max(0, min(roi_frame.shape[1], bx2))
        by1 = max(0, min(roi_frame.shape[0], by1))
        by2 = max(0, min(roi_frame.shape[0], by2))
        
        # Check if this YOLO bounding box overlaps with the tracked hand contour
        is_hand_overlap = False
        if has_hand and hand_box is not None:
            hx1, hy1, hx2, hy2 = hand_box
            ix1 = max(bx1, hx1)
            iy1 = max(by1, hy1)
            ix2 = min(bx2, hx2)
            iy2 = min(by2, hy2)
            inter_area = max(0, ix2 - ix1) * max(0, iy2 - iy1)
            hand_area = max(1, (hx2 - hx1) * (hy2 - hy1))
            overlap_ratio = inter_area / hand_area
            is_hand_overlap = overlap_ratio >= 0.15
        
        status_tag = "VALID" if (c_score >= 0.50 and is_hand_overlap) else ("NO_HAND" if not is_hand_overlap else "LOW_CONF")
        raw_dets.append(f"{name.upper()} ({c_score:.2f} [{status_tag}])")
        
        if c_score >= 0.50 and is_hand_overlap:
            speech_boxes.append(box)
    
    if raw_dets:
        print(f"\r[YOLO Raw]: {', '.join(raw_dets)}                     ", end="", flush=True)

    # --- 5. The Audio & Terminal Logic (with Debouncing and Reset) ---
    current_time = time.time()
    
    if len(speech_boxes) > 0:
        # A sign is detected with confidence >= 0.50 and confirmed skin presence
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