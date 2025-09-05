from flask import Flask
from flask_cors import CORS
from src.routes.analyze_logs import analyze_logs_bp
from src.routes.analyze_images import analyze_images_bp
from src.routes.analyze_combined import analyze_combined_bp  
import os

def create_app():
    app = Flask(__name__)
    
    # Activer CORS ici
    CORS(app)
    print("CORS is enabled!")  # pour vérifier à l'exécution

    # Enregistrer les blueprints
    app.register_blueprint(analyze_logs_bp, url_prefix="/log")
    app.register_blueprint(analyze_images_bp, url_prefix="/images")
    app.register_blueprint(analyze_combined_bp, url_prefix="/combined")
    return app

if __name__ == "__main__":
    app = create_app()
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
