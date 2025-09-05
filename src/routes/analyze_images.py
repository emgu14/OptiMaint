import os
import re
import json
import tempfile
from datetime import datetime
from flask import Blueprint, request, send_file, jsonify, after_this_request
from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer,
    Image as RLImage, PageBreak
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
import google.generativeai as genai
from utils.report import header_footer_factory

# ===============================
# Blueprint Flask
# ===============================
analyze_images_bp = Blueprint("analyze_images", __name__)

# Configure Gemini
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel("gemini-2.5-flash")

# ===============================
# Classe et parsing
# ===============================
class ImageAnalysis:
    def __init__(self, image_name, image_path, title="", labels=None, values=None, conclusion="", recommendation=""):
        self.image_name = image_name
        self.image_path = image_path
        self.title = title
        self.labels = labels or []
        self.values = values or []
        self.conclusion = conclusion
        self.recommendation = recommendation

def clean_text(s: str) -> str:
    if not s:
        return ""
    # Conserver les signes négatifs en début de nombre
    s = re.sub(r"```(?:[\s\S]*?)```", lambda m: m.group(0).strip("`"), s, flags=re.DOTALL)
    s = re.sub(r"^\s*[*•]\s*", "", s, flags=re.MULTILINE)  # Supprimer uniquement les puces, pas les "-"
    return s.strip()

def try_parse_as_json_block(content: str):
    try:
        obj_match = re.search(r"\{[\s\S]*\}", content)
        if obj_match:
            return json.loads(obj_match.group(0))
    except Exception:
        pass
    return None

def parse_gemini_text_to_analysis(content: str, image_name: str, image_path: str) -> ImageAnalysis:
    content = clean_text(content)

    # 1) JSON fallback
    obj = try_parse_as_json_block(content)
    if obj and isinstance(obj, dict):
        title = obj.get("title") or obj.get("Titre") or ""
        labels = obj.get("labels") or obj.get("etiquettes") or obj.get("Labels") or []
        values = obj.get("values") or obj.get("Valeurs") or []
        conclusion = obj.get("conclusion") or obj.get("Conclusion") or ""
        recommendation = obj.get("recommendation") or obj.get("Recommendation") or ""
        # Filtrer les values pour exclure les lignes où une cellule contient exactement "Conclusion" ou "Recommendation"
        values = [v for v in values if not any(x.strip().lower() in ["conclusion", "recommendation"] for x in v)]
        return ImageAnalysis(image_name, image_path, title, labels, values, conclusion, recommendation)

    # 2) Parsing textuel
    title = ""
    m_title = re.search(r"(?im)^\s*(?:Titre|Title)\s*:\s*(.+)$", content)
    if m_title:
        title = m_title.group(1).strip()

    conclusion = ""
    m_conc = re.search(r"(?im)^\s*Conclusion\s*:\s*(.+?)(?=(?:\n\s*Recommendation\s*:|\Z))", content, re.S)
    if m_conc:
        conclusion = clean_text(m_conc.group(1).strip())

    recommendation = ""
    m_recom = re.search(r"(?im)^\s*Recommendation\s*:\s*(.+)$", content, re.S)
    if m_recom:
        recommendation = clean_text(m_recom.group(1).strip())

    labels = []
    values = []
    
    # Priorité 1: Recherche de la structure de tableau (même pour une seule ligne)
    m_labels = re.search(r"(?im)^\s*(?:Labels?|Étiquettes?)\s*:\s*(.+)$", content)
    if m_labels:
        raw_labels = m_labels.group(1)
        labels = [x.strip() for x in re.split(r"[|\t;,]", raw_labels) if x.strip()]
        
        m_lignes = re.search(r"(?im)^\s*(?:Lignes?|Rows?)\s*:\s*(.*)$", content)
        if m_lignes:
            first_row = m_lignes.group(1).strip()
            start_idx = m_lignes.end()
            end_idx = len(content)
            m_next = re.search(r"(?im)^\s*(?:Conclusion|Recommendation)\s*:", content[start_idx:])
            if m_next:
                end_idx = start_idx + m_next.start()
            
            lines_block = content[start_idx:end_idx].strip()
            rows = [ln.strip() for ln in lines_block.splitlines() if ln.strip()]
            
            if first_row:
                rows.insert(0, first_row)
            
            for row in rows:
                # Essayer différents séparateurs pour gérer les tableaux robustement
                row_values = None
                if "|" in row:
                    row_values = [x.strip() for x in row.split("|")]
                elif ";" in row:
                    row_values = [x.strip() for x in row.split(";")]
                elif "," in row:
                    row_values = [x.strip() for x in row.split(",")]
                
                # Vérifier que row_values est cohérent avec le nombre de labels (si labels existe)
                if row_values and (not labels or len(row_values) == len(labels)):
                    # Exclure les lignes contenant "Conclusion" ou "Recommendation"
                    if not any(x.strip().lower() in ["conclusion", "recommendation"] for x in row_values):
                        values.append(row_values)
    
    # Priorité 2: Si aucune structure de tableau valide n'est trouvée, parsing des paires clé-valeur
    if not values or not labels:
        # Améliorer le regex pour capturer les paires clé-valeur correctement
        key_value_pairs = re.findall(r"(?im)^\s*([^:\n]+?)\s*:\s*(.*?)(?=\n\s*[^:\n]+?\s*:|\n\s*Conclusion\s*:|\n\s*Recommendation\s*:|\Z)", content, re.DOTALL)
        if key_value_pairs:
            labels = ["Paramètre", "Valeur"]
            for k, v in key_value_pairs:
                cleaned_k = clean_text(k)
                cleaned_v = v.strip()  # Utiliser v.strip() directement pour éviter de perdre le signe
                if cleaned_k.strip().lower() not in ["titre", "title", "conclusion", "recommendation"]:
                    # Si la valeur contient un autre " : ", la décomposer en sous-paires
                    if ':' in cleaned_v:
                        sub_pairs = re.split(r'\s*:\s*', cleaned_v)
                        if len(sub_pairs) >= 2:
                            # Prendre la première partie comme valeur principale
                            values.append([cleaned_k, sub_pairs[0].strip() if sub_pairs[0].strip() else "Vide"])
                            # Traiter les sous-paires restantes
                            for i in range(1, len(sub_pairs) - 1, 2):
                                sub_key = sub_pairs[i].strip()
                                sub_val = sub_pairs[i + 1].strip() if i + 1 < len(sub_pairs) else "Vide"
                                if sub_key and sub_key.lower() not in ["conclusion", "recommendation"]:
                                    values.append([sub_key, sub_val if sub_val else "Vide"])
                        else:
                            values.append([cleaned_k, cleaned_v if cleaned_v else "Vide"])
                    else:
                        values.append([cleaned_k, cleaned_v if cleaned_v else "Vide"])

    # Nettoyage supplémentaire : si une valeur contient du texte de recommendation, le déplacer
    for i, row in enumerate(values):
        for j, cell in enumerate(row):
            if "recommendation" in cell.lower() or "conseil" in cell.lower():
                # Supposer que c'est une recommendation mal placée
                if not recommendation:
                    recommendation = cell
                row[j] = ""  # Vider la cellule

    return ImageAnalysis(image_name, image_path, title, labels, values, conclusion, recommendation)

# ===============================
# Route Flask
# ===============================
@analyze_images_bp.route("/generatePDF", methods=["POST"])
def generate_pdf():
    if "images" not in request.files:
        return jsonify({"error": "Aucune image fournie"}), 400

    images = request.files.getlist("images")
    results = []
    temp_image_paths = []

    try:
        for image_file in images:
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
            image_path = temp_file.name
            image_file.save(image_path)
            temp_file.close()
            temp_image_paths.append(image_path)

            with PILImage.open(image_path) as img:
                img = img.convert("RGB")
                img.save(image_path)

            prompt = (
                "Tu es un expert en administration WebLogic. Analyse cette capture de configuration.\n"
                " Donne un titre clair sur une ligne commençant par « Titre: ».\n"
                " Si la capture présente un tableau, utilise la structure suivante :\n"
                "  - Indique « Labels: » avec les noms de colonnes séparés par « | ».\n"
                "  - Puis « Lignes: » et liste chaque enregistrement ligne par ligne en séparant les valeurs par « | ». Copie exactement les valeurs sans modification, y compris les signes négatifs des nombres.\n"
                " Si la capture ne présente PAS un tableau, liste les paramètres sous forme « Nom du paramètre : valeur exacte », chaque paire sur une ligne séparée, sans ajouter d'explications, de recommendations ou de texte supplémentaire dans les valeurs. Si une valeur est vide, indique « Vide ». Copie les nombres exactement tels quels, y compris les signes négatifs s'ils existent, sans les convertir en positifs.\n"
                " Ajoute ensuite une ligne « Conclusion: » avec un résumé clair (sans recommendation).\n"
                " Enfin, ajoute une ligne « Recommendation: » avec une recommandation d’expert (sécurité, performance ou configuration).\n"
                " Réponds en français et assure-toi que les signes négatifs des nombres sont préservés exactement comme dans la capture."
            )

            response = model.generate_content(
                [{"mime_type": "image/png", "data": open(image_path, "rb").read()}, prompt]
            )
            content = (response.text or "").strip()
            print(f"Debug - Raw content: {content}")  # Ajout pour débogage
            analysis = parse_gemini_text_to_analysis(content, image_file.filename, image_path)
            results.append(analysis)

        pdf_temp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        pdf_path = pdf_temp.name
        pdf_temp.close()

        doc = SimpleDocTemplate(
            pdf_path, pagesize=A4,
            leftMargin=2*cm, rightMargin=2*cm,
            topMargin=4.5*cm, bottomMargin=2*cm
        )
        styles = getSampleStyleSheet()
        title_style = styles["Heading2"]
        normal = styles["BodyText"]
        normal.fontSize = 10
        small = ParagraphStyle(
            name='SmallTableStyle',
            parent=styles['BodyText'],
            fontSize=9,
            wordWrap='CJK',  # Meilleur wrapping pour les mots longs
            leading=10  # Espacement réduit entre les lignes
        )

        story = []
        for idx, res in enumerate(results):
            if idx > 0:
                story.append(PageBreak())

            story.append(Paragraph(f"📄 Analyse de : <b>{res.image_name}</b>", title_style))
            if res.title:
                story.append(Paragraph(f"<b>Titre :</b> {res.title}", normal))
            story.append(Spacer(1, 1*cm))

            # IMAGE CAPTURE
            try:
                img = RLImage(res.image_path)
                max_w = doc.width
                max_h = A4[1] - (doc.topMargin + doc.bottomMargin + 5*cm)
                orig_w, orig_h = img.wrap(0, 0)
                if orig_w > max_w or orig_h > max_h:
                    ratio = min(max_w / orig_w, max_h / orig_h)
                    img._restrictSize(orig_w * ratio, orig_h * ratio)
                story.append(img)
                story.append(Spacer(1, 1*cm))
            except Exception as img_e:
                story.append(Paragraph(f"Erreur lors de l'ajout de l'image : {img_e}", normal))

            # TABLEAU
            if res.values and all(isinstance(r, list) for r in res.values) and res.labels:
                header = [Paragraph(f"<b>{h}</b>", small) for h in res.labels]
                data = [header]
                data.extend([[Paragraph(str(x), small) for x in row] for row in res.values])
                
                # Calculer les largeurs des colonnes dynamiquement en fonction du contenu
                col_widths = []
                if data:
                    num_cols = len(data[0])
                    # Largeur minimale par colonne réduite à 60%
                    min_width = doc.width / num_cols * 0.6
                    for col in range(num_cols):
                        max_width = 0
                        for row in data:
                            text = row[col].text if hasattr(row[col], 'text') else str(row[col])
                            # Estimer la largeur en fonction du texte (approximation)
                            width = len(text) * 3.5  # Approximation empirique (en points)
                            max_width = max(max_width, width)
                        col_widths.append(max(min_width, min(max_width, doc.width / num_cols)))  # Limite max à 100%
                
                table = Table(data, colWidths=col_widths, repeatRows=1)
                table.setStyle(TableStyle([
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 2),  # Réduit à 2 points
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]))
                story.append(table)
                story.append(Spacer(1, 0.8*cm))

            # AJOUTER CONCLUSION ET RECOMMENDATION COMME PARAGRAPHES
            if res.conclusion:
                story.append(Paragraph(f"<b>Conclusion :</b> {res.conclusion}", normal))
                story.append(Spacer(1, 0.5*cm))
            if res.recommendation:
                story.append(Paragraph(f"<b>Recommendation :</b> {res.recommendation}", normal))
                story.append(Spacer(1, 0.5*cm))

        doc.build(
            story,
            onFirstPage=header_footer_factory(
                date_str=datetime.now().strftime("%d/%m/%Y"),
                title="Rapport d'Audit WebLogic"
            ),
            onLaterPages=header_footer_factory(
                date_str=datetime.now().strftime("%d/%m/%Y"),
                title="Rapport d'Audit WebLogic",
            )
        )

        @after_this_request
        def cleanup(response):
            try:
                if os.path.exists(pdf_path):
                    os.remove(pdf_path)
                for p in temp_image_paths:
                    if os.path.exists(p):
                        os.remove(p)
            except Exception:
                pass
            return response

        return send_file(pdf_path, as_attachment=True, download_name="audit_weblogic.pdf")

    except Exception as e:
        for p in temp_image_paths:
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass
        return jsonify({"error": str(e)}), 500