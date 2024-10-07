from flask import Flask, request, render_template_string, redirect, url_for, send_from_directory
from apscheduler.schedulers.background import BackgroundScheduler
import os
import importlib.util
import time
import sqlite3
import traceback
from werkzeug.utils import secure_filename
from io import StringIO
import sys
import shutil

# Flask app and APScheduler setup
app = Flask(__name__)
scheduler = BackgroundScheduler()
scheduler.start()

UPLOAD_FOLDER = 'uploaded_scripts'
OUTPUT_FOLDER = 'script_outputs'
FILES_FOLDER = 'generated_files'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)
os.makedirs(FILES_FOLDER, exist_ok=True)

# Database setup
DB_FILE = 'jobs.db'
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS jobs (job_id TEXT PRIMARY KEY, filepath TEXT, interval INTEGER)''')
conn.commit()

# In-memory storage for job outputs
job_outputs = {}

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        # Handle file upload
        if 'file' not in request.files:
            return 'No file part'
        file = request.files['file']
        if file.filename == '':
            return 'No selected file'
        if file and file.filename.endswith('.py'):
            filename = secure_filename(file.filename)
            filepath = os.path.join(UPLOAD_FOLDER, filename)
            file.save(filepath)

            # Get the interval from the form
            try:
                interval = int(request.form['interval'])
            except ValueError:
                return 'Interval must be an integer'

            # Add job to scheduler and database
            job_id = f"job_{int(time.time())}"
            scheduler.add_job(run_script_with_error_handling, 'interval', seconds=interval, args=[filepath, job_id], id=job_id)
            cursor.execute('INSERT INTO jobs (job_id, filepath, interval) VALUES (?, ?, ?)', (job_id, filepath, interval))
            conn.commit()

    # Display the uploaded scripts and their schedules
    job_list = []
    for job in scheduler.get_jobs():
        cursor.execute('SELECT filepath, interval FROM jobs WHERE job_id = ?', (job.id,))
        result = cursor.fetchone()
        if result:
            job_list.append((job.id, os.path.basename(result[0]), result[1]))
    
    return render_template_string('''
        <!doctype html>
        <title>Upload Python Script</title>
        <h1>Upload Python Script to Schedule</h1>
        <form method=post enctype=multipart/form-data>
          <input type=file name=file>
          <input type=number name=interval placeholder="Interval in seconds">
          <input type=submit value=Upload>
        </form>
        <h2>Scheduled Jobs</h2>
        <ul>
        {% for job_id, script, interval in job_list %}
          <li>{{ script }} (Interval: {{ interval }} seconds)
            <form method="post" action="{{ url_for('start_job', job_id=job_id) }}" style="display:inline;">
              <button type="submit">Start</button>
            </form>
            <form method="post" action="{{ url_for('stop_job', job_id=job_id) }}" style="display:inline;">
              <button type="submit">Stop</button>
            </form>
            <form method="get" action="{{ url_for('view_output', job_id=job_id) }}" style="display:inline;">
              <button type="submit">View Output</button>
            </form>
            <form method="get" action="{{ url_for('download_output', job_id=job_id) }}" style="display:inline;">
              <button type="submit">Download Output</button>
            </form>
            <form method="get" action="{{ url_for('browse_files', job_id=job_id) }}" style="display:inline;">
              <button type="submit">Browse Files</button>
            </form>
          </li>
        {% endfor %}
        </ul>
    ''', job_list=job_list)

@app.route('/start/<job_id>', methods=['POST'])
def start_job(job_id):
    cursor.execute('SELECT filepath, interval FROM jobs WHERE job_id = ?', (job_id,))
    result = cursor.fetchone()
    if result:
        filepath, interval = result
        if not scheduler.get_job(job_id):
            scheduler.add_job(run_script_with_error_handling, 'interval', seconds=interval, args=[filepath, job_id], id=job_id)
    return redirect(url_for('index'))

@app.route('/stop/<job_id>', methods=['POST'])
def stop_job(job_id):
    job = scheduler.get_job(job_id)
    if job:
        job.remove()
    return redirect(url_for('index'))

@app.route('/output/<job_id>', methods=['GET'])
def view_output(job_id):
    output = job_outputs.get(job_id, "No output available for this job.")
    return render_template_string('''
        <!doctype html>
        <title>Job Output</title>
        <h1>Output for Job {{ job_id }}</h1>
        <pre>{{ output }}</pre>
        <a href="{{ url_for('index') }}">Back to Home</a>
    ''', job_id=job_id, output=output)

@app.route('/download/<job_id>', methods=['GET'])
def download_output(job_id):
    output_file = os.path.join(OUTPUT_FOLDER, f"output_{job_id}.txt")
    if os.path.exists(output_file):
        return send_from_directory(OUTPUT_FOLDER, f"output_{job_id}.txt", as_attachment=True)
    else:
        return f"No output file available for job {job_id}", 404

@app.route('/browse/<job_id>', methods=['GET'])
def browse_files(job_id):
    job_folder = os.path.join(FILES_FOLDER, job_id)
    if not os.path.exists(job_folder):
        return f"No files available for job {job_id}", 404
    files = []
    for root, _, filenames in os.walk(job_folder):
        for filename in filenames:
            relative_path = os.path.relpath(os.path.join(root, filename), job_folder)
            files.append(relative_path)
    return render_template_string('''
        <!doctype html>
        <title>Job Files</title>
        <h1>Files for Job {{ job_id }}</h1>
        <ul>
        {% for file in files %}
          <li>
            <a href="{{ url_for('download_file', job_id=job_id, filename=file) }}">{{ file }}</a>
          </li>
        {% endfor %}
        </ul>
        <a href="{{ url_for('index') }}">Back to Home</a>
    ''', job_id=job_id, files=files)

@app.route('/download/<job_id>/<path:filename>', methods=['GET'])
def download_file(job_id, filename):
    job_folder = os.path.join(FILES_FOLDER, job_id)
    file_path = os.path.join(job_folder, filename)
    if os.path.exists(file_path):
        return send_from_directory(job_folder, filename, as_attachment=True)
    else:
        return f"File {filename} not found for job {job_id}", 404

def run_script_with_error_handling(filepath, job_id):
    # Redirect stdout and stderr to capture script output
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    redirected_output = StringIO()
    sys.stdout = redirected_output
    sys.stderr = redirected_output

    job_folder = os.path.join(FILES_FOLDER, job_id)
    os.makedirs(job_folder, exist_ok=True)

    try:
        # Copy the script to the job folder to ensure it can be found
        job_script_path = os.path.join(job_folder, os.path.basename(filepath))
        shutil.copy(filepath, job_script_path)

        # Load and execute the Python script from the absolute path without changing directories
        spec = importlib.util.spec_from_file_location("uploaded_module", job_script_path)
        uploaded_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(uploaded_module)
    except Exception as e:
        print(f"Error occurred while executing the script {filepath}: {e}")
        traceback.print_exc()
    finally:
        # Restore stdout and stderr
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        # Save the output
        output = redirected_output.getvalue()
        if job_id in job_outputs:
            job_outputs[job_id] += output
            # Keep only the last 100 messages
            job_outputs[job_id] = '\n'.join(job_outputs[job_id].splitlines()[-100:])
        else:
            job_outputs[job_id] = output
        redirected_output.close()

        # Save the output to a file
        output_file = os.path.join(OUTPUT_FOLDER, f"output_{job_id}.txt")
        with open(output_file, 'w') as f:
            f.write(job_outputs[job_id])

if __name__ == '__main__':
    try:
        app.run(debug=True, use_reloader=False)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        conn.close()
