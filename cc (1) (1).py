import cv2
import streamlit as st
from ultralytics import YOLO
from deep_sort_realtime.deepsort_tracker import DeepSort
from datetime import datetime, timedelta
import time
import sqlite3
import json
import streamlit.components.v1 as components

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect("productivity.db", check_same_thread=False)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS productivity (
            person_id INTEGER PRIMARY KEY,
            time_spent_seconds INTEGER,
            last_updated TEXT
        )
    """)
    conn.commit()
    return conn

conn = init_db()

# --- STREAMLIT SETUP ---
st.set_page_config(layout="wide")
st.title("Real-Time Productivity Monitor (Multi-Person Tracking)")

model = YOLO("yolov8n.pt")
tracker = DeepSort(max_age=30)

zones = {
    "Desk Zone": (100, 100, 500, 400),
}

# Session state init
if "person_times" not in st.session_state:
    st.session_state.person_times = {}
if "person_entry_time" not in st.session_state:
    st.session_state.person_entry_time = {}
if "run" not in st.session_state:
    st.session_state.run = False

col1, col2 = st.columns(2)
if col1.button("Start"):
    st.session_state.run = True
if col2.button("Stop"):
    st.session_state.run = False

frame_placeholder = st.empty()
log_placeholder = st.empty()

if st.session_state.run:
    cap = cv2.VideoCapture(0)
    while st.session_state.run:
        ret, frame = cap.read()
        if not ret:
            st.warning("Failed to grab frame from camera.")
            break

        results = model(frame, classes=[0])
        detections = []

        for box in results[0].boxes.xyxy:
            x1, y1, x2, y2 = map(int, box[:4])
            detections.append(([x1, y1, x2 - x1, y2 - y1], 0.9, "person"))

        tracks = tracker.update_tracks(detections, frame=frame)
        now = datetime.now()

        for zx1, zy1, zx2, zy2 in zones.values():
            cv2.rectangle(frame, (zx1, zy1), (zx2, zy2), (255, 0, 0), 2)

        for track in tracks:
            if not track.is_confirmed():
                continue
            track_id = track.track_id
            l, t, r, b = track.to_ltrb()
            cx, cy = int((l + r) / 2), int((t + b) / 2)

            in_zone = False
            for zx1, zy1, zx2, zy2 in zones.values():
                if zx1 <= cx <= zx2 and zy1 <= cy <= zy2:
                    in_zone = True
                    break

            if in_zone:
                if track_id not in st.session_state.person_entry_time:
                    st.session_state.person_entry_time[track_id] = now
                st.session_state.person_times[track_id] = st.session_state.person_times.get(track_id, timedelta()) + timedelta(seconds=1)
            else:
                if track_id in st.session_state.person_entry_time:
                    del st.session_state.person_entry_time[track_id]

            label = f"ID {track_id}"
            cv2.rectangle(frame, (int(l), int(t)), (int(r), int(b)), (0, 255, 0), 2)
            cv2.putText(frame, label, (int(l), int(t - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        frame_placeholder.image(frame_rgb, channels="RGB")

        log_text = "### Time Spent per Person\n"
        for pid, time_spent in st.session_state.person_times.items():
            log_text += f"Person {pid}: {str(time_spent).split('.')[0]}\n"
        log_placeholder.markdown(log_text)

        time.sleep(1)  # Refresh every second

    cap.release()

    # Save data to DB on stop
    # Save data to DB on stop (or when exiting the loop)
def save_data_to_db():
    c = conn.cursor()
    for pid, duration in st.session_state.person_times.items():
        seconds = int(duration.total_seconds())
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Using UPSERT syntax supported by SQLite 3.24+
        c.execute("""
            INSERT INTO productivity (person_id, time_spent_seconds, last_updated)
            VALUES (?, ?, ?)
            ON CONFLICT(person_id) DO UPDATE SET
                time_spent_seconds = productivity.time_spent_seconds + excluded.time_spent_seconds,
                last_updated = excluded.last_updated
        """, (pid, seconds, timestamp))

        print(f"Saved person_id {pid}, seconds {seconds}, timestamp {timestamp}")  # Debug print

    conn.commit()
    print("DB commit done")

# Use it after stopping the webcam loop
if st.session_state.run:
    # existing webcam capture loop here
    ...
else:
    # Only save when stopped
    save_data_to_db()
    st.success("✅ Data saved to DB.")

# --- DASHBOARD ---
def get_data_for_dashboard():
    c = conn.cursor()
    c.execute("SELECT person_id, time_spent_seconds, last_updated FROM productivity")
    rows = c.fetchall()
    data = []
    for pid, seconds, ts in rows:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        time_str = f"{h:02d}:{m:02d}:{s:02d}"
        data.append({"id": pid, "time_spent": time_str, "time_spent_seconds": seconds, "timestamp": ts})
    return data

def generate_dashboard_html(data):
    data_json = json.dumps(data)
    html_code = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8" />
      <title>Real-Time Productivity Dashboard</title>
      <link rel="stylesheet" href="https://cdn.datatables.net/1.13.6/css/jquery.dataTables.min.css">
      <script src="https://code.jquery.com/jquery-3.6.0.min.js"></script>
      <script src="https://cdn.datatables.net/1.13.6/js/jquery.dataTables.min.js"></script>
      <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
      <style>body{{color:white; background-color:#222;}}</style>
    </head>
    <body>
      <h1>Real-Time Productivity Dashboard</h1>
      <table id='productivityTable'><thead><tr><th>ID</th><th>Time Spent</th><th>Timestamp</th></tr></thead><tbody id='table-body'></tbody></table>
      <canvas id='sessionsChart' style="max-width: 700px; max-height: 300px;"></canvas>
      <canvas id='top5Chart' style="max-width: 700px; max-height: 300px; margin-top: 40px;"></canvas>

      <script>
        const data = {data_json};
        const tbody = document.getElementById('table-body');
        data.forEach(item => {{
          const tr = document.createElement('tr');
          tr.innerHTML = `<td>${{item.id}}</td><td>${{item.time_spent}}</td><td>${{item.timestamp}}</td>`;
          tbody.appendChild(tr);
        }});

        $(document).ready(function() {{ $('#productivityTable').DataTable(); }});

        const ctx = document.getElementById('sessionsChart').getContext('2d');
        new Chart(ctx, {{
          type: 'bar',
          data: {{
            labels: data.map(d => 'Person ' + d.id),
            datasets: [{{ label: 'Time Spent (s)', data: data.map(d => d.time_spent_seconds), backgroundColor: 'rgba(0,210,255,0.9)' }}]
          }},
          options: {{ responsive: true, scales: {{ y: {{ beginAtZero: true }} }} }}
        }});

        const top5 = data.sort((a, b) => b.time_spent_seconds - a.time_spent_seconds).slice(0, 5);
        const top5Ctx = document.getElementById('top5Chart').getContext('2d');
        new Chart(top5Ctx, {{
          type: 'bar',
          data: {{
            labels: top5.map(d => 'Person ' + d.id),
            datasets: [{{ label: 'Top Productivity (s)', data: top5.map(d => d.time_spent_seconds), backgroundColor: 'rgba(255,206,86,0.9)' }}]
          }},
          options: {{ responsive: true, scales: {{ y: {{ beginAtZero: true }} }} }}
        }});
      </script>
    </body>
    </html>
    """
    return html_code

if st.button("Show Productivity Dashboard"):
    data = get_data_for_dashboard()
    html_code = generate_dashboard_html(data)

    with open("dashboard.html", "w", encoding="utf-8") as f:
        f.write(html_code)
    st.success("✅ Dashboard saved as dashboard.html")

    components.html(html_code, height=700, scrolling=True)
