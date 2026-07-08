"""Upload to YouTube via the Data API v3 (OAuth installed-app flow).

Supports MULTIPLE channels via separate token files. YouTube uploads to the
channel you select during the browser sign-in, and that choice is saved in the
token file. So each channel (including brand/secondary channels under the same
Google account) gets its own token.

Setup (one-time per Google project):
  1. Google Cloud Console -> enable "YouTube Data API v3".
  2. OAuth consent screen (External, add yourself as a test user).
  3. Create OAuth client ID -> type "Desktop app" -> download JSON.
  4. Save it next to this file as 'client_secret.json'.

Authorize a channel (one-time per channel):
  python youtube_upload.py                      # main channel  -> token.json
  python youtube_upload.py token_halfsent.json  # @HalfSent      -> that file
  -> In the browser, pick/grant access to the channel you want for THAT token.
"""
import os
import sys

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

HERE = os.path.dirname(os.path.abspath(__file__))
# upload to post videos; readonly so we can confirm which channel a token owns.
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]
CLIENT_SECRET = os.path.join(HERE, "client_secret.json")
DEFAULT_TOKEN = "token.json"


def _token_path(token_file=None):
    token_file = token_file or DEFAULT_TOKEN
    return token_file if os.path.isabs(token_file) else os.path.join(HERE, token_file)


def _secret_path(secret_file=None):
    secret_file = secret_file or "client_secret.json"
    return secret_file if os.path.isabs(secret_file) else os.path.join(HERE, secret_file)


def has_client_secret(secret_file=None) -> bool:
    return os.path.exists(_secret_path(secret_file))


def is_authorized(token_file=None) -> bool:
    return os.path.exists(_token_path(token_file))


def _get_credentials(token_file=None, secret_file=None):
    path = _token_path(token_file)
    secret = _secret_path(secret_file)
    creds = None
    if os.path.exists(path):
        # Load with the token's OWN stored scopes so existing upload-only
        # tokens (e.g. the original token.json) keep working unchanged.
        creds = Credentials.from_authorized_user_file(path)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(secret):
                raise FileNotFoundError(
                    f"{os.path.basename(secret)} not found. Add the Google OAuth "
                    f"desktop client JSON next to youtube_upload.py."
                )
            flow = InstalledAppFlow.from_client_secrets_file(secret, SCOPES)
            print("\nA browser will open. Sign in, then SELECT THE CHANNEL you want "
                  "this token to upload to (pick your brand channel if uploading there).\n")
            creds = flow.run_local_server(port=0)
        with open(path, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
    return creds


def channel_info(token_file=None, secret_file=None):
    """Return {'title','id'} for the channel a token owns (best-effort)."""
    try:
        creds = _get_credentials(token_file, secret_file)
        youtube = build("youtube", "v3", credentials=creds)
        resp = youtube.channels().list(part="snippet", mine=True).execute()
        items = resp.get("items", [])
        if items:
            return {"title": items[0]["snippet"]["title"], "id": items[0]["id"]}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}
    return {"error": "no channel found for this token"}


def upload_video(path, title, description, tags, privacy="public",
                 made_for_kids=False, progress=None, token_file=None,
                 secret_file=None):
    """Upload and return the video id + watch URL (+ channel title)."""
    import time
    from googleapiclient.errors import HttpError

    creds = _get_credentials(token_file, secret_file)
    youtube = build("youtube", "v3", credentials=creds)

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": "24",  # Entertainment
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": made_for_kids,
        },
    }

    media = MediaFileUpload(path, chunksize=1024 * 1024, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    retry_count = 0
    max_retries = 5

    while response is None:
        try:
            status, response = request.next_chunk()
            if status and progress:
                progress("upload", f"Uploading... {int(status.progress() * 100)}%")
            # Reset retry count upon successful chunk upload
            retry_count = 0
        except HttpError as e:
            if e.resp.status in [500, 502, 503, 504]:
                retry_count += 1
                if retry_count > max_retries:
                    raise
                if progress:
                    progress("upload", f"Google Server Error ({e.resp.status}). Retrying chunk ({retry_count}/{max_retries})...")
                time.sleep(retry_count * 2)
            else:
                raise
        except (OSError, Exception) as e:
            # Handles socket timeout, ssl EOF, connection drop, protocol errors, etc.
            retry_count += 1
            if retry_count > max_retries:
                raise
            if progress:
                progress("upload", f"Connection lost. Retrying chunk upload ({retry_count}/{max_retries}): {e}")
            time.sleep(retry_count * 2)

    vid = response["id"]
    return {"id": vid, "url": f"https://youtu.be/{vid}"}


def authorize(token_file=None, secret_file=None):
    """Run the OAuth flow once and report which channel the token owns."""
    if not has_client_secret(secret_file):
        sf = secret_file or "client_secret.json"
        print(f"ERROR: {sf} not found next to youtube_upload.py.")
        print("Create OAuth 'Desktop app' credentials in Google Cloud Console,")
        print("enable 'YouTube Data API v3', and save the JSON.")
        return False
    _get_credentials(token_file, secret_file)
    info = channel_info(token_file, secret_file)
    tf = token_file or DEFAULT_TOKEN
    if "error" in info:
        print(f"Authorized -> {tf} (couldn't read channel name: {info['error']})")
    else:
        print(f"Authorized -> {tf}  |  Channel: {info['title']} ({info['id']})")
    return True


if __name__ == "__main__":
    # Usage: python youtube_upload.py [token_file] [client_secret_file]
    arg_token = sys.argv[1] if len(sys.argv) > 1 else None
    arg_secret = sys.argv[2] if len(sys.argv) > 2 else None
    authorize(arg_token, arg_secret)
