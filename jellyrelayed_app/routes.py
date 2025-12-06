import os
import time
import logging
import threading
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, 
    send_from_directory, session, abort, current_app, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash

from . import config as app_config
from .main import (
    handle_new_file, is_video_file, RECENTLY_PROCESSED,
    send_test_notification_with_mock_data
)
from .clients import JellyfinClient

logger = logging.getLogger(__name__)
bp = Blueprint('main', __name__)

# ==========================================
# AUTHENTICATION & ROUTE PROTECTION
# ==========================================
@bp.before_app_request
def before_request_hook():
    config = app_config.load_config()
    
    # If no admin user is configured, allow access only to the setup page
    if not config.get('username') or not config.get('password_hash'):
        if request.endpoint and 'setup' not in request.endpoint and 'static' not in request.endpoint:
            return redirect(url_for('main.setup'))

    # If an admin is configured, protect all routes except for login, webhooks, and static files
    elif 'logged_in' not in session:
        if request.endpoint and \
           'login' not in request.endpoint and \
           'static' not in request.endpoint and \
           'webhook' not in request.endpoint:
            return redirect(url_for('main.login'))

# ==========================================
# WEB UI ROUTES
# ==========================================
@bp.route('/')
def index():
    return redirect(url_for('main.jellyfin'))

@bp.route('/jellyfin')
def jellyfin():
    config = app_config.load_config()
    return render_template('general.html', config=config)

@bp.route('/notifications')
def notifications():
    config = app_config.load_config()
    return render_template('notifications.html', config=config)

@bp.route('/monitoring')
def monitoring():
    config = app_config.load_config()
    folders = get_folder_list(app_config.MEDIA_ROOT)
    return render_template('monitoring.html', config=config, folders=folders)

@bp.route('/webhook_info')
def webhook_info():
    config = app_config.load_config()
    return render_template('webhook.html', config=config)

@bp.route('/logs')
def logs():
    config = app_config.load_config()
    log_file_path = '/data/jellyrelayed.log'
    try:
        with open(log_file_path, 'r') as f:
            # Read the last 1000 lines
            lines = f.readlines()[-1000:]
            log_content = "".join(lines)
    except FileNotFoundError:
        log_content = "Log file not found."
    except Exception as e:
        log_content = f"Error reading log file: {e}"
    return render_template('logs.html', config=config, logs=log_content)

@bp.route('/about')
def about():
    config = app_config.load_config()
    return render_template('about.html', config=config)

@bp.route('/setup', methods=['GET', 'POST'])
def setup():
    config = app_config.load_config()
    if config.get('username') and config.get('password_hash'):
        flash("An admin account is already configured. Please log in.", "warning")
        return redirect(url_for('main.login'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        password_confirm = request.form.get('password_confirm')

        if not all([username, password, password_confirm]):
            flash("All fields are required.", "error")
            return render_template('setup.html'), 400
        
        if password != password_confirm:
            flash("Passwords do not match.", "error")
            return render_template('setup.html'), 400

        config['username'] = username
        config['password_hash'] = generate_password_hash(password)
        app_config.save_config(config)
        
        flash("Admin account created successfully. Please log in.", "success")
        return redirect(url_for('main.login'))

    return render_template('setup.html')

@bp.route('/login', methods=['GET', 'POST'])
def login():
    config = app_config.load_config()
    if not config.get('username') or not config.get('password_hash'):
        flash("No admin account found. Please create one.", "info")
        return redirect(url_for('main.setup'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == config.get('username') and check_password_hash(config.get('password_hash', ''), password):
            session['logged_in'] = True
            session.permanent = True
            flash("Logged in successfully!", "success")
            return redirect(url_for('main.jellyfin'))
        else:
            flash("Invalid username or password.", "error")
    
    return render_template('login.html')

@bp.route('/logout')
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for('main.login'))

@bp.route('/save', methods=['POST'])
def save():
    config = app_config.load_config()
    data = request.get_json() # Get JSON data

    # Define a helper function to return JSON response
    def json_response(success, message, status_code=200):
        # Removed Flask flash, now handled by frontend JS
        return jsonify({"success": success, "message": message}), status_code
    
    try: # Wrap the entire save logic in a try-except block
        action = data.get('action') # Get action from JSON
        
        if action == 'regenerate_api_key':
            new_key = app_config.secrets.token_hex(16)
            config['security_api_key'] = new_key
            app_config.save_config(config)
            return jsonify({"success": True, "message": "A new Security API Key has been generated!", "new_key": new_key})
        elif action == 'save_general':
            config['base_url'] = data.get('base_url', '').rstrip('/')
            config['jellyfin_url'] = data.get('jellyfin_url')
            config['jellyfin_api_key'] = data.get('jellyfin_api_key')
            app_config.save_config(config)
            return json_response(True, "General settings saved successfully!")
        elif action == 'save_notifications':
            config['pushover_app_token'] = data.get('pushover_app_token')
            config['pushover_user_key'] = data.get('pushover_user_key')
            
            if 'notification_options' not in config:
                config['notification_options'] = {}
            
            for type_key in ['episode', 'movie']: # Renamed 'type' to 'type_key' to avoid conflict with Python's built-in type
                if type_key not in config['notification_options']:
                    config['notification_options'][type_key] = {}
                config['notification_options'][type_key]['title_format'] = data.get(f'title_format_{type_key}', '')
                config['notification_options'][type_key]['include_overview'] = data.get(f'include_overview_{type_key}', False)
                config['notification_options'][type_key]['include_codec'] = data.get(f'include_codec_{type_key}', False)
                config['notification_options'][type_key]['include_filesize'] = data.get(f'include_filesize_{type_key}', False)
                config['notification_options'][type_key]['include_path'] = data.get(f'include_path_{type_key}', False)
                config['notification_options'][type_key]['include_poster'] = data.get(f'include_poster_{type_key}', False)
                config['notification_options'][type_key]['use_emojis'] = data.get(f'use_emojis_{type_key}', False)

            app_config.save_config(config)
            return json_response(True, "Notification settings saved successfully!")
        elif action == 'save_monitoring':
            if 'libraries' not in config:
                config['libraries'] = {}
            for name in config.get('libraries', {}):
                config['libraries'][name]['scan_enabled'] = data.get(f'lib_scan_enabled_{name}', False)
                config['libraries'][name]['notify_enabled'] = data.get(f'lib_notify_enabled_{name}', False)
                config['libraries'][name]['device'] = data.get(f'lib_device_{name}', '')
                # Convert to int, ensure it's not None
                priority_val = data.get(f'lib_prio_{name}')
                config['libraries'][name]['priority'] = int(priority_val) if priority_val is not None else 0 
                config['libraries'][name]['watch_path'] = data.get(f'lib_path_{name}', '')
            app_config.save_config(config)
            return json_response(True, "Monitoring settings saved successfully!")
        else:
            return json_response(False, "Unknown action.", 400)
    except Exception as e:
        logger.error(f"Error in save route: {e}", exc_info=True)
        return json_response(False, f"An unexpected error occurred: {e}", 500)

@bp.route('/scan', methods=['POST'])
def scan():
    success, message = scan_libraries_and_update_config()
    return jsonify({"success": success, "message": message})

@bp.route('/logo')
def logo():
    return send_from_directory(os.path.join(current_app.static_folder), 'icon.png', mimetype='image/png')

@bp.route('/test_notification', methods=['POST'])
def test_notification():
    config = app_config.load_config()
    pushover_app_token = config.get('pushover_app_token')
    pushover_user_key = config.get('pushover_user_key')

    if not pushover_app_token or not pushover_user_key:
        flash("Pushover APP Token or User Key is not configured. Please configure it first.", "error")
        # When called via JS/AJAX, we need to return a JSON response, not a redirect
        return jsonify({"success": False, "message": "Pushover APP Token or User Key is not configured."}), 400

    data = request.get_json()
    notification_type = data.get('type') # 'episode' or 'movie'
    mock_item = data.get('mock_item') # Fictional data for the item
    notification_options_from_form = data.get('notification_options') # Options from the form

    if not notification_type or not mock_item or not notification_options_from_form:
        return jsonify({"success": False, "message": "Missing required data for test notification."}), 400

    # Override the config's notification options with the ones from the form for this test
    # This ensures the preview matches the test notification
    temp_config_opts = config['notification_options'][notification_type].copy()
    for key, value in notification_options_from_form.items():
        temp_config_opts[key] = value

    success, message = send_test_notification_with_mock_data(
        pushover_app_token, 
        pushover_user_key,
        notification_type,
        mock_item,
        temp_config_opts # Use the combined options
    )
    
    if success:
        flash(message, "success")
        return jsonify({"success": True, "message": message}), 200
    else:
        flash(message, "error")
        return jsonify({"success": False, "message": message}), 400
    
# ==========================================
# WEBHOOK ROUTES
# ==========================================
@bp.route('/webhook', methods=['POST'])
def webhook_missing_key():
    logger.error(f"🚨 Webhook received at the base /webhook URL from {request.remote_addr}. The full URL was {request.url}. It must include the API key (e.g., /webhook/YOUR_API_KEY).")
    abort(401, "API key is missing from the webhook URL.")

@bp.route('/webhook/<string:api_key>', methods=['POST'])
def webhook(api_key):
    config = app_config.load_config()
    if not app_config.secrets.compare_digest(api_key, config.get('security_api_key')):
        logger.warning(f"🚨 Unauthorized webhook attempt from {request.remote_addr} rejected.")
        abort(401, "Invalid security API key.")

    data = request.json
    event_type = data.get('eventType', 'N/A')
    is_upgrade = data.get('isUpgrade', False)
    logger.info(f"📬 Webhook received! Event: '{event_type}', Upgrade: {is_upgrade}.")
    
    filepaths = []
    base_folder = None

    if 'series' in data and 'path' in data['series']:
        base_folder = data['series']['path']
    
    if 'movieFile' in data and 'path' in data['movieFile']:
        filepaths.append(data['movieFile']['path'])
    elif base_folder and 'episodeFile' in data and 'relativePath' in data['episodeFile']:
        filepaths.append(os.path.join(base_folder, data['episodeFile']['relativePath']))
    elif base_folder and 'episodeFiles' in data:
        for episode_file in data.get('episodeFiles', []):
            if 'relativePath' in episode_file:
                filepaths.append(os.path.join(base_folder, episode_file['relativePath']))

    if not filepaths:
        logger.info("🧐 Webhook received, but no processable file paths were found in the payload.")
        return "Webhook processed, but no files to act on.", 200

    processed_count = 0
    for path in filepaths:
        now = time.time()
        if path in RECENTLY_PROCESSED and (now - RECENTLY_PROCESSED[path]) < app_config.DEDUPE_WINDOW_SECONDS:
            logger.info(f"🔄 Duplicate notification for '{os.path.basename(path)}' received within {app_config.DEDUPE_WINDOW_SECONDS}s. Ignoring.")
            continue
        
        RECENTLY_PROCESSED[path] = now
        if path and is_video_file(path):
            logger.info(f"▶️ Found new file to process: {path}")
            threading.Thread(target=handle_new_file, args=(path, is_upgrade)).start()
            processed_count += 1
        elif path:
            logger.info(f"Ignoring non-video file: {path}")
    
    # Clean up old entries from the deduplication cache
    expired_time = time.time() - app_config.DEDUPE_WINDOW_SECONDS
    for path, timestamp in list(RECENTLY_PROCESSED.items()):
        if timestamp < expired_time:
            del RECENTLY_PROCESSED[path]

    if processed_count > 0:
        return f"Accepted {processed_count} files for background processing.", 202
    return "Webhook received, but no new video files to process.", 200

# ==========================================
# HELPER FUNCTIONS FOR ROUTES
# ==========================================
def get_folder_list(root_path, max_depth=2):
    """Scans for subdirectories under a given path up to a max depth."""
    folder_list = []
    if not os.path.exists(root_path):
        return folder_list
    folder_list.append(root_path)
    try:
        root_depth = root_path.rstrip(os.path.sep).count(os.path.sep)
        for dirpath, dirnames, _ in os.walk(root_path, topdown=True):
            current_depth = dirpath.rstrip(os.path.sep).count(os.path.sep) - root_depth
            if current_depth >= max_depth:
                del dirnames[:] # Stop descending further
                continue
            for dirname in dirnames:
                folder_list.append(os.path.join(dirpath, dirname))
    except Exception as e:
        logger.error(f"Error scanning folders: {e}")
    folder_list.sort()
    return folder_list

def scan_libraries_and_update_config():
    """Scans for Jellyfin libraries and updates the config.
    
    Returns:
        (bool, str): A tuple of (success, message).
    """
    try:
        config = app_config.load_config()
        url = config.get('jellyfin_url')
        key = config.get('jellyfin_api_key')

        if not url or not key:
            return False, "Jellyfin URL or API Key is not configured."

        client = JellyfinClient(url, key)
        users, err = client.get_users()
        if err:
            return False, f"Error connecting to Jellyfin: {err}"

        if not users:
            return False, "No users found on Jellyfin server."

        user_id = users[0]['Id']
        views, err = client.get_views(user_id)
        if err:
            return False, f"Error fetching libraries: {err}"

        current_libs = config.get('libraries', {})
        new_libs = {}
        for v in views:
            name = v['Name']
            new_libs[name] = current_libs.get(name, {
                "scan_enabled": True, 
                "notify_enabled": True, 
                "device": "", 
                "priority": 0, 
                "watch_path": ""
            })

        config['libraries'] = new_libs
        config['cached_user_id'] = user_id
        app_config.save_config(config)
        return True, "Successfully scanned and updated libraries from Jellyfin! The page will now refresh."
    except Exception as e:
        logger.error(f"Error during library scan: {e}", exc_info=True)
        return False, f"An unexpected error occurred during the scan: {e}"
