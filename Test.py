import tkinter as tk
from tkinter import ttk, messagebox
import cv2
from PIL import Image, ImageTk
import requests
import urllib.request
import numpy as np
import threading
import time

class RoverControlApp:
    def __init__(self, root):
        self.root = root
        self.root.title("ESP32 Rover Dashboard")
        self.root.geometry("700x680")
        self.root.configure(bg="#1e1e1e")

        # Connection State
        self.dev_ip = ""
        self.cam_ip = ""
        self.is_streaming = False
        self.current_state = "stop"

        # --- Top Connection Panel ---
        conn_frame = tk.Frame(root, bg="#2d2d2d", padx=10, pady=10)
        conn_frame.pack(fill="x", padx=10, pady=10)

        tk.Label(conn_frame, text="Dev ESP32 IP:", fg="white", bg="#2d2d2d").grid(row=0, column=0, padx=5, pady=5)
        self.dev_ip_entry = tk.Entry(conn_frame, width=15)
        self.dev_ip_entry.insert(0, "192.168.137.9") # Default Dev IP
        self.dev_ip_entry.grid(row=0, column=1, padx=5, pady=5)

        tk.Label(conn_frame, text="CAM ESP32 IP:", fg="white", bg="#2d2d2d").grid(row=0, column=2, padx=5, pady=5)
        self.cam_ip_entry = tk.Entry(conn_frame, width=15)
        self.cam_ip_entry.insert(0, "192.168.137.106") # Default CAM IP
        self.cam_ip_entry.grid(row=0, column=3, padx=5, pady=5)

        self.btn_connect = tk.Button(conn_frame, text="Start Video", bg="#00adb5", fg="white", 
                                     font=("Arial", 10, "bold"), command=self.toggle_stream)
        self.btn_connect.grid(row=0, column=4, padx=10, pady=5)

        # --- Video Display Panel ---
        self.video_label = tk.Label(root, text="[ Video Stream Offline ]", bg="black", fg="white", font=("Arial", 14))
        self.video_label.pack(fill="both", expand=True, padx=10, pady=5)

        # --- Control Panel ---
        ctrl_frame = tk.Frame(root, bg="#1e1e1e")
        ctrl_frame.pack(fill="x", pady=15)

        self.btn_forward = tk.Button(ctrl_frame, text="▲ FORWARD (W)", bg="#00adb5", fg="white", 
                                     font=("Arial", 14, "bold"), width=18, height=2)
        self.btn_forward.pack(pady=5)

        self.btn_backward = tk.Button(ctrl_frame, text="▼ BACKWARD (S)", bg="#00adb5", fg="white", 
                                      font=("Arial", 14, "bold"), width=18, height=2)
        self.btn_backward.pack(pady=5)

        # Bind Mouse Buttons
        self.btn_forward.bind("<ButtonPress-1>", lambda e: self.send_cmd_thread("backward"))
        self.btn_forward.bind("<ButtonRelease-1>", lambda e: self.send_cmd_thread("stop"))
        
        self.btn_backward.bind("<ButtonPress-1>", lambda e: self.send_cmd_thread("forward"))
        self.btn_backward.bind("<ButtonRelease-1>", lambda e: self.send_cmd_thread("stop"))

        # Bind Keyboard Keys
        root.bind("<KeyPress-w>", lambda e: self.handle_key_press("backward"))
        root.bind("<KeyRelease-w>", lambda e: self.handle_key_release())
        root.bind("<KeyPress-Up>", lambda e: self.handle_key_press("backward"))
        root.bind("<KeyRelease-Up>", lambda e: self.handle_key_release())

        root.bind("<KeyPress-s>", lambda e: self.handle_key_press("forward"))
        root.bind("<KeyRelease-s>", lambda e: self.handle_key_release())
        root.bind("<KeyPress-Down>", lambda e: self.handle_key_press("forward"))
        root.bind("<KeyRelease-Down>", lambda e: self.handle_key_release())

        root.protocol("WM_DELETE_WINDOW", self.on_close)

    # --- HTTP Motor Controls ---
    def send_cmd_thread(self, command):
        if self.current_state == command:
            return
        self.current_state = command
        threading.Thread(target=self._send_http, args=(command,), daemon=True).start()

    def _send_http(self, command):
        dev_ip = self.dev_ip_entry.get().strip()
        if not dev_ip:
            return
        url = f"http://{dev_ip}/{command}"
        try:
            requests.get(url, timeout=0.5)
        except Exception:
            pass

    def handle_key_press(self, command):
        self.send_cmd_thread(command)

    def handle_key_release(self):
        self.send_cmd_thread("stop")

    # --- Video Stream Processing ---
    def toggle_stream(self):
        if not self.is_streaming:
            # Clean IP string
            raw_ip = self.cam_ip_entry.get().strip()
            self.cam_ip = raw_ip.replace("http://", "").split(":")[0]

            if not self.cam_ip:
                messagebox.showerror("Error", "Please enter ESP32-CAM IP")
                return

            self.is_streaming = True
            self.btn_connect.config(text="Stop Video", bg="#ff4d4d")
            threading.Thread(target=self.update_video_feed, daemon=True).start()
        else:
            self.stop_stream()

    def update_video_feed(self):
        stream_url = f"http://{self.cam_ip}:81/stream"
        try:
            # Open HTTP MJPEG Connection
            stream = urllib.request.urlopen(stream_url, timeout=5)
            bytes_data = b''

            while self.is_streaming:
                bytes_data += stream.read(2048)
                
                # Search for JPEG Start (\xff\xd8) and End (\xff\xd9)
                a = bytes_data.find(b'\xff\xd8')
                b = bytes_data.find(b'\xff\xd9')

                if a != -1 and b != -1:
                    jpg = bytes_data[a:b+2]
                    bytes_data = bytes_data[b+2:]
                    
                    # Decode frame
                    frame = cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR)
                    
                    if frame is not None:
                        frame = cv2.resize(frame, (640, 480))
                        cv2image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        img = Image.fromarray(cv2image)
                        imgtk = ImageTk.PhotoImage(image=img)

                        self.video_label.imgtk = imgtk
                        self.video_label.config(image=imgtk)
        except Exception as e:
            print("Stream Error:", e)
            if self.is_streaming:
                messagebox.showerror("Stream Error", f"Cannot connect to stream.\nMake sure Chrome tab is CLOSED!\n\nDetails: {e}")
            self.stop_stream()

    def stop_stream(self):
        self.is_streaming = False
        self.btn_connect.config(text="Start Video", bg="#00adb5")
        self.video_label.config(image="", text="[ Video Stream Offline ]")

    def on_close(self):
        self.send_cmd_thread("stop")
        self.stop_stream()
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = RoverControlApp(root)
    root.mainloop()