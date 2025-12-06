import os
import logging
from logging.handlers import RotatingFileHandler
from jellyrelayed_app.main import create_app
from jellyrelayed_app import config as app_config

# Ensure data directory exists before starting
os.makedirs('/data', exist_ok=True)

# Setup logging
log_file = '/data/jellyrelayed.log'
logging.basicConfig(
    level=logging.INFO,
    format=app_config.LOG_FORMAT,
    handlers=[
        RotatingFileHandler(log_file, maxBytes=1024 * 1024, backupCount=5),
        logging.StreamHandler()
    ]
)

# Ensure data directory exists before starting
os.makedirs('/data', exist_ok=True)

# Create and run the Flask app
app = create_app()

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)
