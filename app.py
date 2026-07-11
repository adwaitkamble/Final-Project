import cv2
from ultralytics import YOLO
import pyttsx3
import threading
import queue
import time

# --- 1. The Background Speech Worker ---
speech_queue = queue.Queue()

def speech_worker():
    # SAPI5 engine requires COM library initialization when used in background threads on Windows
    try:
        import pythoncom
        pythoncom.CoInitialize()
    except Exception as e:
        print(f"[AUDIO THREAD COM INIT WARN]: {e}")
        
    engine = pyttsx3.init()
    engine.setProperty('rate', 150)
    while True:
        text = speech_queue.get()
        if text is None: break
        try:
            # We added a debug print here so you know the audio thread is working!
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

# --- 3. Speech Tracking Variables ---
last_spoken_word = ""
last_spoken_time = 0
cooldown_seconds = 5.0 

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
    
    # FIXED: Added verbose=False to keep the terminal output clean
    results = model(roi_frame, conf=0.25, verbose=False)

    # --- 5. The Audio & Terminal Logic ---
    if len(results[0].boxes) > 0:
        class_id = int(results[0].boxes[0].cls[0].item())
        detected_word = results[0].names[class_id]
        
        current_time = time.time()
        
        # New Sign Detected
        if detected_word != last_spoken_word:
            print(f"\n[DETECTED]: {detected_word.upper()}")
            speech_queue.put(detected_word)
            last_spoken_word = detected_word
            last_spoken_time = current_time
            
        # Held Sign for 5 Seconds
        elif (current_time - last_spoken_time) >= cooldown_seconds:
            print(f"\n[REPEAT]: {detected_word.upper()}")
            speech_queue.put(detected_word)
            last_spoken_time = current_time

    annotated_roi = results[0].plot()
    frame[y1:y2, x1:x2] = annotated_roi
    
    cv2.imshow("Marathi Sign Language", frame)

    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

cap.release()
cv2.destroyAllWindows()