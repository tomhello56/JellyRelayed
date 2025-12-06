import os
import time
import logging
import threading
from flask import Flask, current_app
from werkzeug.security import generate_password_hash, check_password_hash

from . import config as app_config
from .clients import JellyfinClient, PushoverClient

logger = logging.getLogger(__name__)

# Global store for deduplicating recent file processing requests
RECENTLY_PROCESSED = {}

def create_app():
    """Creates and configures the Flask application."""
    app = Flask(__name__, template_folder='../templates', static_folder='../templates/static')
    app.secret_key = os.urandom(24)

    # Load initial config
    with app.app_context():
        app.config['jellyrelayed'] = app_config.load_config()

    from . import routes
    app.register_blueprint(routes.bp)

    return app

def handle_new_file(filepath, is_upgrade=False):
    """
    Handles the logic for processing a new file notification.
    This runs in a background thread.
    """
    config = app_config.load_config()
    jellyfin_url = config.get('jellyfin_url')
    jellyfin_key = config.get('jellyfin_api_key')
    user_id = config.get('cached_user_id')

    if not jellyfin_url or not jellyfin_key or not user_id:
        logger.warning("🚫 Jellyfin settings are incomplete, skipping file processing.")
        return

    client = JellyfinClient(jellyfin_url, jellyfin_key)
    
    # 1. Identify Target Library
    target_library_id, target_library_name, lib_conf = resolve_target_library(client, user_id, config, filepath)

    # 2. Trigger Scan
    scan_enabled = lib_conf.get('scan_enabled', True) if lib_conf else True
    if scan_enabled:
        if target_library_id:
            logger.info(f"🎯 Found matching library '{target_library_name}'. Triggering targeted scan.")
            client.refresh_library(target_library_id)
        else:
            logger.info(f"🗺️ No specific library matched. Triggering a global library scan.")
            client.refresh_all_libraries()
    else:
        logger.info(f"🚫 Scanning for library '{target_library_name}' is disabled. Skipping scan.")
        # We still continue to notification part

    # 3. Poll for Metadata
    target_filename = os.path.basename(filepath)
    found_item = poll_for_metadata(client, user_id, target_filename)
    
    if found_item:
        logger.info(f"✅ Metadata found for '{target_filename}'!")
        send_notification(found_item, config, client, user_id, filepath, is_upgrade)
    else:
        logger.warning(f"⏰ Timeout! Jellyfin didn't provide metadata for '{target_filename}' in time.")


def resolve_target_library(client, user_id, config, filepath):
    """Determines the target Jellyfin library based on the file path."""
    try:
        views, err = client.get_views(user_id)
        if err:
            logger.error(err)
            return None, "Global", None

        filepath_norm = os.path.normpath(filepath).lower()
        
        for name, conf in config.get('libraries', {}).items():
            watch_path = conf.get('watch_path', '')
            if watch_path:
                watch_path_basename = os.path.basename(os.path.normpath(watch_path)).lower()
                # Check if the watch path's base folder is in the new file's path
                if f'{os.path.sep}{watch_path_basename}{os.path.sep}' in filepath_norm:
                    for v in views:
                        if v['Name'] == name:
                            return v['Id'], name, conf
    except Exception as e:
        logger.error(f"Error mapping library: {e}")

    return None, "Global", None # Default to global scan

def poll_for_metadata(client, user_id, target_filename):
    """Polls Jellyfin for a new item's metadata to appear."""
    logger.info(f"⏳ Waiting for Jellyfin to process '{target_filename}'...")
    for _ in range(36):  # Poll for up to 3 minutes (36 * 5 seconds)
        time.sleep(5)
        items, err = client.get_latest_items(user_id)
        if err:
            logger.error(err)
            continue # Continue polling even if there's a temporary error
        
        for item in items:
            # Check if the filename is in the path or name of a recent item
            if target_filename in item.get('Path', '') or target_filename in item.get('Name', ''):
                # Check if the overview is populated, a good sign that metadata is ready
                if item.get('Overview'):
                    return item
    return None

def send_notification(item, config, client, user_id, filepath, is_upgrade=False):
    """Prepares and sends a notification for the processed item."""
    pushover_client = PushoverClient(config.get('pushover_app_token'), config.get('pushover_user_key'))
    
    item_type = 'movie' if item.get('Type') == 'Movie' else 'episode'
    opts = config.get('notification_options', {}).get(item_type, {})

    # Resolve routing to see if this library should send notifications
    routing = resolve_notification_routing(client, user_id, config, item, filepath)
    
    item_name = item.get('Name', 'Unknown')
    if item_type == 'episode':
        item_name = f"{item.get('SeriesName')} - {item.get('Name', 'Unknown Episode')}"

    if not routing.get('notify_enabled', True):
        logger.info(f"🚫 Notification for '{item_name}' skipped because notifications for library '{routing['source_lib']}' are disabled.")
        return

    logger.info(f"📬 Preparing to send notification for '{item_name}' (via library '{routing['source_lib']}')")
    
    title = _format_title(item, is_upgrade, opts)
    message = _format_message(item, filepath, opts)
    
    # Get image for the notification
    image_data = None
    if opts.get('include_poster', False):
        img_id = item.get('SeriesId') or item.get('SeasonId') or item['Id']
        image_data = client.get_item_image(img_id)

    pushover_client.send_notification(
        title, 
        message, 
        image_data, 
        routing.get('device'), 
        routing.get('priority', 0)
    )

def send_test_notification_with_mock_data(app_token, user_key, notification_type, mock_item, opts):
    """
    Sends a test notification using provided mock data and options.
    This function acts as a wrapper to format the mock data for _format_title and _format_message.
    """
    pushover_client = PushoverClient(app_token, user_key)

    # _format_title expects 'item' to be a dictionary with keys like 'Type', 'SeriesName', etc.
    # We need to map our mock_item to this structure.
    # Also, 'is_upgrade' is hardcoded to False for test notifications.
    is_upgrade = False

    if notification_type == 'episode':
        item_for_formatting = {
            'Type': 'Episode',
            'SeriesName': mock_item.get('series_name'),
            'ParentIndexNumber': int(mock_item.get('season_num', '00')),
            'IndexNumber': int(mock_item.get('episode_num', '00')),
            'Name': mock_item.get('episode_name'),
            'Overview': mock_item.get('overview'),
            'MediaSources': [{
                'MediaStreams': [{
                    'Codec': mock_item.get('codec')
                }]
            }]
        }
    else: # movie
        item_for_formatting = {
            'Type': 'Movie',
            'Name': mock_item.get('movie_name'),
            'ProductionYear': mock_item.get('movie_year'),
            'Overview': mock_item.get('overview'),
            'MediaSources': [{
                'MediaStreams': [{
                    'Codec': mock_item.get('codec')
                }]
            }]
        }
    
    title = _format_title(item_for_formatting, is_upgrade, opts)
    
    # _format_message expects 'item' and 'filepath'. Filepath can be taken from mock_item.
    message = _format_message(item_for_formatting, mock_item.get('path', ''), opts, mock_filesize=mock_item.get('filesize'))

    image_data = None
    if opts.get('include_poster', False) and mock_item.get('poster_url'):
        # For test notifications, we fetch the image directly from the static URL
        try:
            # The URL from mock_item['poster_url'] will be something like '/static/images/tvshow.png'
            # We need to get the actual file path on the server.
            # current_app.static_folder points to the root of the static files ('../templates/static')
            # mock_item['poster_url'].split('/static/')[-1] gives 'images/tvshow.png'
            static_file_relative_path = mock_item['poster_url'].split('/static/')[-1]
            static_file_full_path = os.path.join(current_app.static_folder, static_file_relative_path)
            
            with open(static_file_full_path, 'rb') as f:
                image_data = f.read()
            logger.info(f"Successfully loaded test poster image from {static_file_full_path}")
        except Exception as e:
            logger.error(f"Error fetching test poster image from {mock_item.get('poster_url')}: {e}")
            image_data = None # Ensure it's None if there's an error

    try:
        pushover_client.send_notification(title, message, image_data=image_data)
        return True, "Test notification sent successfully!"
    except Exception as e:
        logger.error(f"Failed to send test notification: {e}")
        return False, f"Failed to send test notification: {e}"


def _send_test_pushover_notification(app_token, user_key, device=None, priority=0):
    """Sends a generic test Pushover notification."""
    pushover_client = PushoverClient(app_token, user_key)
    title = "JellyRelayed Test Notification"
    message = "This is a test notification from JellyRelayed."
    
    try:
        pushover_client.send_notification(title, message, device=device, priority=priority)
        return True, "Test notification sent successfully!"
    except Exception as e:
        return False, f"Failed to send test notification: {e}"


def resolve_notification_routing(client, user_id, config, item, filepath=None):
    """Finds the correct notification settings (device, priority, etc.) for a given item."""
    libs = config.get('libraries', {})
    default_routing = {"notify_enabled": True, "device": None, "priority": 0, "source_lib": "Global Default"}

    # 1. Try to match based on the original file path
    if filepath:
        filepath_norm = os.path.normpath(filepath).lower()
        for lib_name, lib_conf in libs.items():
            watch_path = lib_conf.get('watch_path', '')
            if watch_path and f'{os.path.sep}{os.path.basename(os.path.normpath(watch_path)).lower()}{os.path.sep}' in filepath_norm:
                return {
                    "notify_enabled": lib_conf.get('notify_enabled', True),
                    "device": lib_conf.get('device'),
                    "priority": int(lib_conf.get('priority', 0)),
                    "source_lib": f"{lib_name} (Folder Match)"
                }

    # 2. If no path match, try to match by traversing up the Jellyfin item tree
    try:
        views, err = client.get_views(user_id)
        if err: return default_routing
        views_map = {v['Id']: v['Name'] for v in views}
    except:
        return default_routing

    check_item, found_lib_name = item, None
    for _ in range(5): # Check up to 5 levels deep
        parent_id = check_item.get('ParentId') or check_item.get('SeasonId') or check_item.get('SeriesId')
        if check_item.get('Id') in views_map:
            found_lib_name = views_map[check_item['Id']]
            break
        if parent_id in views_map:
            found_lib_name = views_map[parent_id]
            break
        if not parent_id: break
        
        # Get the parent item to continue traversal
        parent_item, err = client.get_item(parent_id, user_id)
        if err or not parent_item: break
        check_item = parent_item
        
    if found_lib_name and found_lib_name in libs:
        lib_conf = libs[found_lib_name]
        return {
            "notify_enabled": lib_conf.get('notify_enabled', True),
            "device": lib_conf.get('device'),
            "priority": int(lib_conf.get('priority', 0)),
            "source_lib": f"{found_lib_name} (Library Match)"
        }
    


def _format_filesize(filepath):
    """Formats a filesize in bytes to a human-readable string."""
    if not os.path.exists(filepath):
        return "N/A"
    size_bytes = os.path.getsize(filepath)
    if size_bytes == 0:
        return "0B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    i = int(logging.math.floor(logging.math.log(size_bytes, 1024)))
    p = logging.math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"

def _format_title(item, is_upgrade, opts):
    """Formats the notification title based on user-defined format string."""
    prefix = "⏫" if is_upgrade else "✨"
    title_format = opts.get('title_format', '')
    
    if item['Type'] == 'Episode':
        context = {
            "prefix": prefix,
            "series_name": item.get('SeriesName', 'N/A'),
            "season_num": f"{item.get('ParentIndexNumber', 0):02d}",
            "episode_num": f"{item.get('IndexNumber', 0):02d}",
            "episode_name": item.get('Name', 'N/A')
        }
    else: # Movie
        context = {
            "prefix": prefix,
            "movie_name": item.get('Name', 'N/A'),
            "movie_year": item.get('ProductionYear', 'N/A')
        }
        
    return title_format.format(**context)

def _format_message(item, filepath, opts, mock_filesize=None):
    """Formats the notification body based on user-defined options."""
    message_blocks = [] # List to hold formatted text blocks

    # Default emojis (not user-configurable anymore)
    default_emojis = {
        'overview': "📝",
        'codec': "🎞️",
        'filesize': "💾",
        'path': "📁"
    }
    use_emojis = opts.get('use_emojis', False)

    # --- 1. Description (Overview) ---
    overview_content = ""
    if opts.get('include_overview', False):
        overview = item.get('Overview', 'No overview available.').strip()
        if overview:
            if len(overview) > 250:
                overview = overview[:250].rsplit(' ', 1)[0] + "..."
            
            emoji = default_emojis['overview'] if use_emojis else ""
            overview_content = (f"{emoji} " if emoji else "") + overview
            message_blocks.append(overview_content)

    # --- 2. Details (Codec, Filesize, Path) ---
    details_added = False # Flag to track if any detail has been added

    # Codec
    if opts.get('include_codec', False):
        if overview_content and not details_added: # Add blank line if overview exists and this is the first detail
            message_blocks.append("")
        try:
            codec = item['MediaSources'][0]['MediaStreams'][0]['Codec']
            emoji = default_emojis['codec'] if use_emojis else ""
            message_blocks.append((f"{emoji} " if emoji else "") + f"Codec: {codec.upper()}")
            details_added = True
        except (IndexError, KeyError):
            pass

    # Filesize
    if opts.get('include_filesize', False):
        if overview_content and not details_added: # Add blank line if overview exists and this is the first detail
            message_blocks.append("")
        filesize_str = ""
        if mock_filesize:
            filesize_str = f"Size: {mock_filesize}"
        else:
            filesize_str = f"Size: {_format_filesize(filepath)}"
        emoji = default_emojis['filesize'] if use_emojis else ""
        message_blocks.append((f"{emoji} " if emoji else "") + filesize_str)
        details_added = True

    # Path
    if opts.get('include_path', False) and filepath:
        if overview_content and not details_added: # Add blank line if overview exists and this is the first detail
            message_blocks.append("")
        emoji = default_emojis['path'] if use_emojis else ""
        message_blocks.append((f"{emoji} " if emoji else "") + f"Path: {filepath}")
        details_added = True # Not strictly needed here, but for consistency if other details followed

    return "\n".join(message_blocks)

def is_video_file(filename):
    """Checks if a file is a video based on its extension."""
    video_exts = ['.mkv', '.mp4', '.avi', '.mov', '.wmv', '.m4v', '.ts', '.webm']
    return any(filename.lower().endswith(ext) for ext in video_exts)