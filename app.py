import os
import json
import uuid
from io import BytesIO
from datetime import datetime
from flask import Flask, request, jsonify, render_template, send_from_directory, abort, send_file
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
import pandas as pd

# ----- Config -----
ALLOWED_TRACKS = {'AP', 'RP', 'Common', 'LI', 'ES'}
ALLOWED_STATUS = {'Open', 'Fixed', 'Deployed', 'Closed'}
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'pdf'}
MAX_ATTACHMENTS = 5

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///issues.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 25 * 1024 * 1024
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'uploads')

db = SQLAlchemy(app)
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# ----- DB Model -----
class Issue(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    track = db.Column(db.String(20), nullable=False)
    summary = db.Column(db.String(250), nullable=False)
    description = db.Column(db.Text)
    attachments = db.Column(db.Text)  # JSON list of filenames
    raised_by = db.Column(db.String(100))
    assignee = db.Column(db.String(100))
    status = db.Column(db.String(20), default="Open", nullable=False)
    scenario_id = db.Column(db.String(50))
    step_no = db.Column(db.String(20))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "track": self.track,
            "summary": self.summary,
            "description": self.description,
            "attachments": json.loads(self.attachments) if self.attachments else [],
            "raised_by": self.raised_by,
            "assignee": self.assignee,
            "status": self.status,
            "scenario_id": self.scenario_id,
            "step_no": self.step_no,
            "created_at": self.created_at.isoformat() + "Z",
        }

with app.app_context():
    db.create_all()

# ----- Helpers -----
def allowed_file(filename: str) -> bool:
    if '.' not in filename:
        return False
    ext = filename.rsplit('.', 1)[1].lower()
    return ext in ALLOWED_EXTENSIONS

def save_attachments(files):
    saved = []
    for f in files[:MAX_ATTACHMENTS]:
        if f and f.filename and allowed_file(f.filename):
            unique = f"{uuid.uuid4().hex}_{secure_filename(f.filename)}"
            f.save(os.path.join(app.config['UPLOAD_FOLDER'], unique))
            saved.append(unique)
    return saved

# ----- Routes -----
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/issues", methods=["GET"])
def get_issues():
    issues = Issue.query.order_by(Issue.id.desc()).all()
    return jsonify([i.to_dict() for i in issues])

@app.route("/issues", methods=["POST"])
def add_issue():
    data = request.form
    track = (data.get("track") or "").strip()
    status = (data.get("status") or "Open").strip()
    summary = (data.get("summary") or "").strip()
    description = (data.get("description") or "").strip()
    raised_by = (data.get("raised_by") or "").strip()
    assignee = (data.get("assignee") or "").strip()
    scenario_id = (data.get("scenario_id") or "").strip()
    step_no = (data.get("step_no") or "").strip()

    if track not in ALLOWED_TRACKS:
        return jsonify({"error": "Invalid track"}), 400
    if status not in ALLOWED_STATUS:
        return jsonify({"error": "Invalid status"}), 400
    if not summary or len(summary) > 250:
        return jsonify({"error": "Summary is required and must be ≤ 250 chars"}), 400

    files = request.files.getlist("attachments")
    if len(files) > MAX_ATTACHMENTS:
        return jsonify({"error": f"Max {MAX_ATTACHMENTS} attachments allowed"}), 400

    filenames = save_attachments(files)

    issue = Issue(
        track=track,
        summary=summary,
        description=description,
        attachments=json.dumps(filenames),
        raised_by=raised_by,
        assignee=assignee,
        status=status,
        scenario_id=scenario_id,
        step_no=step_no
    )
    db.session.add(issue)
    db.session.commit()
    return jsonify(issue.to_dict()), 201

@app.route("/issues/<int:issue_id>", methods=["PATCH"])
def update_issue(issue_id):
    issue = Issue.query.get_or_404(issue_id)
    data = request.form or request.json

    for field in ["track", "summary", "description", "raised_by", "assignee", "status", "scenario_id", "step_no"]:
        if field in data:
            if field=="status" and data[field] not in ALLOWED_STATUS:
                return jsonify({"error": "Invalid status"}), 400
            setattr(issue, field, (data[field] or "").strip())

    new_files = request.files.getlist("attachments")
    if new_files:
        existing = json.loads(issue.attachments) if issue.attachments else []
        issue.attachments = json.dumps(existing + save_attachments(new_files))

    db.session.commit()
    return jsonify(issue.to_dict())

@app.route("/issues/<int:issue_id>", methods=["DELETE"])
def delete_issue(issue_id):
    issue = Issue.query.get_or_404(issue_id)
    files = json.loads(issue.attachments) if issue.attachments else []
    for f in files:
        try:
            os.remove(os.path.join(app.config['UPLOAD_FOLDER'], f))
        except FileNotFoundError:
            pass
    db.session.delete(issue)
    db.session.commit()
    return jsonify({"message": "Issue deleted"})

@app.route("/issues/<int:issue_id>/attachments/<filename>", methods=["DELETE"])
def delete_attachment(issue_id, filename):
    issue = Issue.query.get_or_404(issue_id)
    files = json.loads(issue.attachments) if issue.attachments else []
    if filename in files:
        files.remove(filename)
        issue.attachments = json.dumps(files)
        db.session.commit()
        try:
            os.remove(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        except FileNotFoundError:
            pass
    return jsonify(issue.to_dict())

@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    safe_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if not os.path.isfile(safe_path):
        abort(404)
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route("/issues/download")
def download_issues():
    issues = Issue.query.order_by(Issue.id.desc()).all()
    data = []
    for i in issues:
        d = i.to_dict()
        d["attachments"] = ", ".join(d["attachments"])
        data.append(d)
    df = pd.DataFrame(data)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name="Issues")
    output.seek(0)
    return send_file(output,
                     download_name="issues.xlsx",
                     as_attachment=True,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ----- Entry Point -----
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
