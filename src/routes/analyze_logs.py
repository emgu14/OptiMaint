# src/routes/analyze_logs.py

import os
import re
import uuid
import json
import tempfile
from typing import List, Dict
from flask import Blueprint, request, jsonify, send_file, after_this_request
from pydantic import BaseModel, ValidationError
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from utils.report import header_footer_factory

# ========================
# Gemini LLM Client
# ========================
try:
    import google.generativeai as genai
except ImportError:
    genai = None


class GeminiAnswer(BaseModel):
    reformulated: str
    solution: str


class GeminiClient:
    def __init__(self, language: str = "fr", model_name: str = "gemini-2.5-flash"):
        self.language = language
        self.model_name = model_name
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.enabled = bool(self.api_key) and genai is not None
        if self.enabled:
            genai.configure(api_key=self.api_key)
            self.model = genai.GenerativeModel(self.model_name)
        else:
            self.model = None

    def suggest_solution(self, error_message: str) -> GeminiAnswer:
        if not self.enabled:
            return GeminiAnswer(
                reformulated="⚠ Gemini désactivé, impossible de reformuler.",
                solution="⚠ Vérifier la clé API et l'installation de la librairie."
            )

        prompt = f"""
        Tu es un expert WebLogic. Résume ce message de log pour qu'il soit clair et concis, expliquant l'erreur comme un expert, puis propose une solution courte et actionnable.
        Message de log: {error_message}
        Réponds en JSON avec les champs:
        {{
          "reformulated": "phrase courte explicative",
          "solution": "solution concise"
        }}
        """
        try:
            res = self.model.generate_content(prompt)
            text = (res.text or "").strip()

            match = re.search(r"\{[\s\S]*\}", text)
            if match:
                return GeminiAnswer(**json.loads(match.group()))
            else:
                return GeminiAnswer(
                    reformulated="Aucune reformulation reçue",
                    solution="Vérifier le message et le contexte manuellement"
                )
        except Exception as e:
            return GeminiAnswer(
                reformulated=f"Échec Gemini: {e}",
                solution=f"Échec Gemini: {e}"
            )


# ========================
# Log Parser
# ========================

ERROR_PATTERNS = [
    r"\bERROR\b.*",
    r"\bException\b.*",
    r"\bFATAL\b.*",
    r"\bSEVERE\b.*",
    r"Traceback \(most recent call last\):.*",
    r"\bCaused by:\b.*",
]

NORMALIZERS = [
    (r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:,\d+)?", "<TIMESTAMP>"),
    (r"\b\d{2}:\d{2}:\d{2}\b", "<TIME>"),
    (r"\b\d+\b", "<NUM>"),
    (r"0x[0-9a-fA-F]+", "<HEX>"),
    (r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "<IP>"),
    (r"[A-Za-z]:\\[^\s]+|/[^\s]+", "<PATH>"),
    (r"\bPID=\d+\b", "PID=<NUM>"),
    (r"\bthread-\d+\b", "thread-<NUM>"),
    (r"\s+", " "),
]
compiled_patterns = [re.compile(p) for p in ERROR_PATTERNS]


def normalize_message(msg: str) -> str:
    text = msg
    for pat, repl in NORMALIZERS:
        text = re.sub(pat, repl, text)
    return text.strip()


def get_context(lines: List[str], index: int, before: int = 3, after: int = 3) -> str:
    start = max(0, index - before)
    end = min(len(lines), index + after + 1)
    return "\n".join(lines[start:end])


def stable_signature(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]", "_", text)[:50]


def parse_log_file(path: str) -> List[Dict]:
    groups = {}
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
        for i, line in enumerate(lines):
            line = line.rstrip("\n")
            if any(p.search(line) for p in compiled_patterns):
                normalized = normalize_message(line)
                sig = stable_signature(normalized)
                if sig not in groups:
                    groups[sig] = {
                        "type": sig,
                        "representative_message": line.strip(),
                        "examples": [],
                        "count": 0,
                        "severity": "MEDIUM",
                    }
                groups[sig]["count"] += 1
                groups[sig]["examples"].append({
                    "original_message": line.strip(),
                    "lineNumber": i + 1,
                    "context": get_context(lines, i)
                })
    return list(groups.values())


# ========================
# Blueprint & Route
# ========================

analyze_logs_bp = Blueprint("analyze_logs", __name__)

class ProcessLogQuery(BaseModel):
    language: str = "fr"
    top_k: int | None = None
    min_count: int | None = None


@analyze_logs_bp.route("/processLogFile", methods=["POST"])
def process_log_file():
    files = request.files.getlist("files")
    if not files:
        return jsonify(error="No files uploaded"), 400

    temp_dir = tempfile.gettempdir()
    saved_files = []

    payload = {
        "language": request.form.get("language", "fr"),
        "top_k": request.form.get("top_k", None),
        "min_count": request.form.get("min_count", None),
    }

    try:
        q = ProcessLogQuery(**payload)
    except ValidationError as e:
        return jsonify(error="Invalid parameters", details=e.errors()), 400

    gemini = GeminiClient(language=q.language)
    all_files_groups = []

    for file in files:
        saved_path = os.path.join(temp_dir, f"{uuid.uuid4()}_{file.filename}")
        file.save(saved_path)
        saved_files.append(saved_path)

        groups = parse_log_file(saved_path)
        if q.min_count:
            groups = [g for g in groups if g["count"] >= q.min_count]
        groups.sort(key=lambda g: g["count"], reverse=True)
        if q.top_k:
            groups = groups[:q.top_k]

        for g in groups:
            answer = gemini.suggest_solution(g["representative_message"])
            g["representative_message"] = answer.reformulated
            g["solution"] = answer.solution

        all_files_groups.append({"filename": file.filename, "groups": groups})

    # Générer PDF avec entête + sections séparées par fichier
    report_id = str(uuid.uuid4())
    pdf_path = os.path.join(temp_dir, f"report_{report_id}.pdf")

    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin= 4.5 * cm,   # laisser de la place pour l'entête
        bottomMargin= 2 * cm,
    )

    styles = getSampleStyleSheet()
    wrap = ParagraphStyle(
        name="Wrap", parent=styles["Normal"],
        fontSize=9, leading=12, wordWrap="CJK",
    )
    story = [Paragraph("Rapport d'analyse des fichiers log", styles["Title"]), Spacer(1, 0.7* cm)]

    for file_data in all_files_groups:
        story.append(Paragraph(f"Fichier: <b>{file_data['filename']}</b>", styles["Heading2"]))
        story.append(Spacer(1, 0.2 * cm))
        groups = file_data["groups"]
        if not groups:
            story.append(Paragraph("Aucune erreur trouvée", styles["Normal"]))
            story.append(Spacer(1, 0.2 * cm))
            continue

        # Nouvel ordre : Message → Solution → Occurrences
        data = [["Message représentatif", "Solution suggérée", "Occurrences"]]
        for g in groups:
            msg = Paragraph(g.get("representative_message", ""), wrap)
            sol = Paragraph(g.get("solution", ""), wrap)
            occ = str(g.get("count", 0))
            data.append([msg, sol, occ])

        table = Table(data, colWidths=[7 * cm, 7 * cm, 2.5 * cm], repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightblue),  # bleu au lieu de gris
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(table)
        story.append(Spacer(1, 1* cm))

    # Appliquer header/footer
    doc.build(story, onFirstPage=header_footer_factory(title="Rapport de Maintenance Préventive"),
              onLaterPages=header_footer_factory(title="Rapport de Maintenance Préventive") )

    for f in saved_files:
        if os.path.exists(f):
            os.remove(f)

    @after_this_request
    def remove_file(response):
        try:
            if os.path.exists(pdf_path):
                os.remove(pdf_path)
        except Exception:
            pass
        return response

    return send_file(
        pdf_path,
        as_attachment=True,
        download_name=f"report.pdf",
        mimetype="application/pdf"
    )
