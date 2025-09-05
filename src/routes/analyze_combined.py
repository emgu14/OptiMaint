from flask import Blueprint, request, send_file, jsonify, after_this_request, current_app
import os
import tempfile
from datetime import datetime
from PyPDF2 import PdfMerger

analyze_combined_bp = Blueprint("analyze_combined", __name__)

@analyze_combined_bp.route("/generateCombinedPDF", methods=["POST"])
def generate_combined_pdf():
    if "images" not in request.files or "files" not in request.files:
        return jsonify({"error": "Une image et un fichier log sont requis"}), 400

    image_file = request.files["images"]
    log_file = request.files["files"]

    temp_dir = tempfile.gettempdir()
    # Utiliser uniquement le nom de fichier pour éviter les chemins invalides
    temp_image_path = os.path.join(temp_dir, f"temp_{os.path.basename(image_file.filename)}")
    temp_log_path = os.path.join(temp_dir, f"temp_{os.path.basename(log_file.filename)}")
    saved_files = [temp_image_path, temp_log_path]

    # Sauvegarder les fichiers avec vérification
    try:
        image_file.save(temp_image_path)
        log_file.save(temp_log_path)
    except OSError as e:
        return jsonify({"error": f"Erreur lors de l'enregistrement des fichiers : {str(e)}"}), 500

    pdf_files = []

    # Appeler les routes existantes via un client de test
    with current_app.test_client() as client:
        image_response = client.post(
            "/images/generatePDF",
            data={"images": (temp_image_path, image_file.filename)},
            content_type="multipart/form-data"
        )
        log_response = client.post(
            "/log/processLogFile",
            data={"files": (temp_log_path, log_file.filename)},
            content_type="multipart/form-data"
        )

        if image_response.status_code == 200:
            img_pdf = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
            img_pdf.write(image_response.data)
            img_pdf.close()
            pdf_files.append(img_pdf.name)
        else:
            return jsonify({"error": "Échec de la génération du PDF image", "status": image_response.status_code}), 500

        if log_response.status_code == 200:
            log_pdf = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
            log_pdf.write(log_response.data)
            log_pdf.close()
            pdf_files.append(log_pdf.name)
        else:
            return jsonify({"error": "Échec de la génération du PDF log", "status": log_response.status_code}), 500

    if not pdf_files:
        return jsonify({"error": "Aucun PDF généré"}), 500

    # Fusionner les PDF
    combined_pdf_path = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf").name
    merger = PdfMerger()
    for pdf in pdf_files:
        merger.append(pdf)
    merger.write(combined_pdf_path)
    merger.close()

    @after_this_request
    def cleanup(response):
        try:
            for f in [combined_pdf_path] + pdf_files + saved_files:
                if os.path.exists(f):
                    os.remove(f)
        except Exception:
            pass
        return response

    return send_file(combined_pdf_path, as_attachment=True, download_name="combined_audit_weblogic.pdf")
