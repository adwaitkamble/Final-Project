# pyrefly: ignore [missing-import]
import pyttsx3
import time

print("Initializing engine in main thread...")
engine = pyttsx3.init()
print("Saying 'Hello from main thread'...")
engine.say("Hello from main thread")
engine.runAndWait()
print("Main thread speech done.")

print("Starting background thread test...")
import threading
import queue

speech_queue = queue.Queue()

def worker():
    print("Background thread: Initializing engine...")
    # SAPI5 on Windows often requires pythoncom.CoInitialize() in background threads
    try:
        import pythoncom
        pythoncom.CoInitialize()
        print("CoInitialize successful.")
    except Exception as e:
        print(f"CoInitialize error (might not be installed): {e}")

    try:
        bg_engine = pyttsx3.init()
        print("Background engine initialized.")
        while True:
            text = speech_queue.get()
            if text is None:
                break
            print(f"Background thread: Attempting to say '{text}'")
            bg_engine.say(text)
            bg_engine.runAndWait()
            print("Background thread: Speech finished.")
            speech_queue.task_done()
    except Exception as e:
        print(f"Background thread error: {e}")

t = threading.Thread(target=worker, daemon=True)
t.start()

time.sleep(1)
speech_queue.put("Hello from background thread")
time.sleep(3)
speech_queue.put(None)
print("Finished test.")
