import os
from datetime import datetime
from reportlab.platypus import Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas
from PIL import Image

# === Factory pour l'en-tête et le footer ===
def header_footer_factory(date_str=None, title="Rapport", logo_left=None, logo_right=None):
    if not date_str:
        date_str = datetime.now().strftime("%Y/%m/%d")

    if not logo_right:
        logo_right = os.path.join(os.path.dirname(__file__), "logo.jpeg")

    def draw_logo(canvas, logo_path, x, y, max_w, max_h):
        """Dessine le logo en conservant les proportions et centré dans son espace."""
        if not os.path.exists(logo_path):
            return
        try:
            img = Image.open(logo_path)
            w, h = img.size
            ratio = min(max_w / w, max_h / h)
            width = w * ratio
            height = h * ratio
            canvas.drawImage(
                logo_path,
                x + (max_w - width) / 2,
                y + (max_h - height) / 2,
                width=width,
                height=height,
                preserveAspectRatio=True
            )
        except Exception as e:
            print(f"Erreur logo {logo_path} :", e)

    def header_footer(canvas, doc):
        canvas.saveState()
        width, height = A4

        # Contenu gauche et centre
        left_content = date_str if not logo_left else ""
        center_content = title

        # === Tableau principal (3 colonnes, 1 ligne) ===
        col_widths = [5 * cm, 9 * cm, 5 * cm]
        row_height = 2 * cm
        data = [[left_content, center_content, ""]]

        table = Table(data, colWidths=col_widths, rowHeights=row_height)
        table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
            ("ALIGN", (0, 0), (-2, 0), "CENTER"),
            ("VALIGN", (0, 0), (-2, 0), "MIDDLE"),
            ("FONTNAME", (0, 0), (-2, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-2, 0), 11),
        ]))

        # Position du tableau
        x = (width - sum(col_widths)) / 2
        y = height - 3 * cm
        table.wrapOn(canvas, width, height)
        table.drawOn(canvas, x, y)

        # === Logo gauche ===
        if logo_left:
            draw_logo(canvas, logo_left, x, y, max_w=5*cm, max_h=row_height)

        # === Logo droit ===
        right_x = x + 5 * cm + 9 * cm
        if logo_right:
    # hauteur max = row_height moins un petit espace vertical
              max_w = 5 * cm
              max_h = row_height - 0.3 * cm
    # On ajuste y pour centrer le logo dans la cellule entière
              adjusted_y = y + (row_height - max_h) / 2
              draw_logo(canvas, logo_right, right_x, adjusted_y, max_w=max_w, max_h=max_h)


        canvas.restoreState()

    return header_footer
