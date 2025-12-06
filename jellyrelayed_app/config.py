import os
import json
import secrets
import logging

logger = logging.getLogger(__name__)

# ==========================================
# CONFIGURATION & CONSTANTS
# ==========================================
CONFIG_FILE = '/data/config.json'
MEDIA_ROOT = '/media'
LOG_FORMAT = '%(asctime)s - %(levelname)s - %(message)s'
DEDUPE_WINDOW_SECONDS = 10 # Ignore duplicate filepaths received within this window

# Default Config Structure
DEFAULT_CONFIG = {
    "base_url": "",
    "jellyfin_url": "",
    "jellyfin_api_key": "",
    "pushover_app_token": "",
    "pushover_user_key": "",
    "security_api_key": "",
    "username": "",
    "password_hash": "",
    "libraries": {},
    "notification_options": {
        "episode": {
            "title_format": "✨ New Episode: {series_name} S{season_num}E{episode_num} - {episode_name}",
            "include_overview": True,
            "include_codec": False,
            "include_filesize": False,
            "include_path": False,
            "include_poster": True,
            "use_emojis": True
        },
        "movie": {
            "title_format": "✨ New Movie: {movie_name} ({movie_year})",
            "include_overview": True,
            "include_codec": False,
            "include_filesize": False,
            "include_path": False,
            "include_poster": True,
            "use_emojis": True
        }
    }
}

def save_config(config):
    """Saves the configuration dictionary to the config file."""
    try:
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=4)
    except Exception as e:
        logger.error(f"Error saving config file: {e}")


def load_config():
    """Loads the configuration from the config file, creating it with defaults if it doesn't exist."""
    if not os.path.exists(CONFIG_FILE):
        config = DEFAULT_CONFIG.copy()
        config['security_api_key'] = secrets.token_hex(16)
        save_config(config)
        return config
    try:
        with open(CONFIG_FILE, 'r') as f:
            data = json.load(f)
            # Ensure all default keys exist
            is_updated = False
            for k, v in DEFAULT_CONFIG.items():
                if k not in data:
                    data[k] = v
                    is_updated = True
            
            # Ensure notification_options is a dictionary
            if not isinstance(data.get('notification_options'), dict):
                data['notification_options'] = DEFAULT_CONFIG['notification_options']
                is_updated = True

            # Ensure notification_options sub-keys exist and are dictionaries
            for type_key in ['episode', 'movie']:
                if not isinstance(data['notification_options'].get(type_key), dict):
                    data['notification_options'][type_key] = DEFAULT_CONFIG['notification_options'][type_key]
                    is_updated = True
                else:
                    # Merge missing sub-keys for episode and movie
                    for k, v in DEFAULT_CONFIG['notification_options'][type_key].items():
                        if k not in data['notification_options'][type_key]:
                            data['notification_options'][type_key][k] = v
                            is_updated = True

            # Ensure a security API key exists
            if not data.get('security_api_key'):
                data['security_api_key'] = secrets.token_hex(16)
                is_updated = True

            if is_updated:
                save_config(data)
                
            return data
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Config file is corrupt or unreadable: {e}. Loading default config.")
        return DEFAULT_CONFIG.copy()
    except Exception as e:
        logger.error(f"An unexpected error occurred while loading the config: {e}")
        return DEFAULT_CONFIG.copy()