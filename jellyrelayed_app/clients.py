import requests
import logging

logger = logging.getLogger(__name__)

class JellyfinClient:
    """A client for interacting with the Jellyfin API."""
    def __init__(self, base_url, api_key):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.headers = {'X-Emby-Token': self.api_key, 'Content-Type': 'application/json'}

    def get_users(self):
        """Fetches all users from the Jellyfin server."""
        try:
            response = requests.get(f"{self.base_url}/Users", headers=self.headers, timeout=5)
            response.raise_for_status()
            return response.json(), None
        except requests.RequestException as e:
            logger.error(f"Failed to connect to Jellyfin to get users: {e}")
            return None, f"Failed to connect to Jellyfin: {e}"

    def get_views(self, user_id):
        """Fetches all library views for a given user."""
        try:
            response = requests.get(f"{self.base_url}/Users/{user_id}/Views", headers=self.headers, timeout=5)
            response.raise_for_status()
            return response.json().get('Items', []), None
        except requests.RequestException as e:
            logger.error(f"Failed to fetch libraries from Jellyfin: {e}")
            return None, f"Failed to fetch libraries: {e}"

    def get_item(self, item_id, user_id):
        """Fetches a single item by its ID."""
        try:
            response = requests.get(f"{self.base_url}/Users/{user_id}/Items/{item_id}", headers=self.headers, timeout=5)
            response.raise_for_status()
            return response.json(), None
        except requests.RequestException as e:
            return None, f"Failed to get item {item_id}: {e}"
            
    def get_latest_items(self, user_id, limit=25):
        """Fetches the latest items for a given user."""
        params = {
            'Recursive': 'true',
            'IncludeItemTypes': 'Movie,Episode',
            'SortBy': 'DateCreated',
            'SortOrder': 'Descending',
            'Limit': limit,
            'Fields': 'Path,DateCreated,ProviderIds,ParentId,SeriesId,SeasonId,MediaSources,Overview'
        }
        try:
            response = requests.get(f"{self.base_url}/Users/{user_id}/Items", headers=self.headers, params=params, timeout=5)
            response.raise_for_status()
            return response.json().get('Items', []), None
        except requests.RequestException as e:
            logger.error(f"Failed to get latest items from Jellyfin: {e}")
            return [], f"Failed to get latest items: {e}"

    def refresh_library(self, library_id):
        """Triggers a scan for a specific library."""
        url = f"{self.base_url}/Items/{library_id}/Refresh?Recursive=true&ImageRefreshMode=Default&MetadataRefreshMode=Default&ReplaceAllMetadata=false"
        try:
            requests.post(url, headers=self.headers, timeout=10)
            return True
        except requests.RequestException as e:
            logger.error(f"Failed to trigger library scan for library {library_id}: {e}")
            return False

    def refresh_all_libraries(self):
        """Triggers a global library scan."""
        url = f"{self.base_url}/Library/Refresh"
        try:
            requests.post(url, headers=self.headers, timeout=10)
            return True
        except requests.RequestException as e:
            logger.error(f"Failed to trigger global library scan: {e}")
            return False
            
    def get_item_image(self, item_id):
        """Gets the primary image for a given item."""
        try:
            response = requests.get(f"{self.base_url}/Items/{item_id}/Images/Primary", headers=self.headers, timeout=5)
            if response.status_code == 200:
                return response.content
        except requests.RequestException as e:
            logger.error(f"Failed to get image for item {item_id}: {e}")
        return None


class PushoverClient:
    """A client for sending notifications via Pushover."""
    API_URL = "https://api.pushover.net/1/messages.json"

    def __init__(self, app_token, user_key):
        self.app_token = app_token
        self.user_key = user_key

    def send_notification(self, title, message, image_data=None, device=None, priority=0):
        """Sends a notification."""
        if not self.app_token or not self.user_key:
            logger.warning("Pushover credentials are not configured. Skipping notification.")
            return

        data = {
            "token": self.app_token,
            "user": self.user_key,
            "title": title,
            "message": message,
            "html": 1,
            "priority": priority
        }
        if device:
            data['device'] = device

        files = {"attachment": ("poster.jpg", image_data, "image/jpeg")} if image_data else {}

        try:
            response = requests.post(self.API_URL, data=data, files=files, timeout=10)
            response.raise_for_status()
            logger.info(f"🎉 Notification sent successfully for: {title}")
        except requests.RequestException as e:
            logger.error(f"Pushover notification failed: {e}")
