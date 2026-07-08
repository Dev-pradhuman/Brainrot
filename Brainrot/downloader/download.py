"""ReelNexus Batch Background Video Downloader

Prompts the user for 5 URLs per category, then downloads them sequentially 
into the appropriate niche folders using yt-dlp, Node.js, and local cookies.
"""
import os
import sys
import shutil
import subprocess

# Colorama for beautiful terminal formatting (pre-installed in .venv)
try:
    from colorama import Fore, Back, Style, init
    init(autoreset=True)
except ImportError:
    # Fallback to no formatting if not available
    class DummyColor:
        def __getattr__(self, name):
            return ""
    Fore = Back = Style = DummyColor()

# Ensure yt-dlp and its default plugins/dependencies are installed
try:
    import yt_dlp
    import yt_dlp_ejs
except ImportError:
    print("yt-dlp or yt-dlp-ejs not found. Installing them automatically...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-U", "yt-dlp[default]", "yt-dlp-ejs"])
    import yt_dlp

try:
    import imageio_ffmpeg
except ImportError:
    print("imageio-ffmpeg not found. Installing it automatically...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "imageio-ffmpeg"])
    import imageio_ffmpeg

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = r"G:\My Drive\Brainrot-videos"

# Niches definition: Display Name -> Subdirectory folder
NICHES = {
    "Reddit Storytelling": "Bg_games",
    "Simpsons": "Bg_simpsons",
    "Cold Story": "Bg_cold",
    "Relationship Advice": "Bg_relationship",
    "Horror Stories": "Bg_horror",
    "Anime": "Bg_anime",
    "Betrayal": "Bg_betrayal",
    "Funny Stories": "Bg_funny",
    "Gaming Reactions": "Bg_games",
}

BROWSERS = {
    "1": ("Chrome", "chrome"),
    "2": ("Edge", "edge"),
    "3": ("Firefox", "firefox"),
    "4": ("Brave", "brave"),
    "5": ("Safari", "safari"),
}

def clear_console():
    os.system('cls' if os.name == 'nt' else 'clear')

def main():
    clear_console()
    print(Fore.MAGENTA + Style.BRIGHT + "==================================================")
    print(Fore.CYAN + Style.BRIGHT + "       REELNEXUS BATCH BACKGROUND DOWNLOADER")
    print(Fore.MAGENTA + Style.BRIGHT + "==================================================")
    print("This utility will ask for up to 5 YouTube URLs for each niche.")
    print("Leave a line blank and press Enter to skip a niche or finish entering URLs.\n")

    # Resolve Cookies
    cookie_file = None
    browser_name = None
    local_cookies = os.path.join(HERE, "cookies.txt")
    
    if os.path.exists(local_cookies):
        print(Fore.GREEN + f"[Cookies] Found local 'cookies.txt'. Using it automatically.\n")
        cookie_file = local_cookies
    else:
        print(Fore.YELLOW + "YouTube requires browser cookies to prevent bot blocks & speed throttling:")
        print("  1. Export cookies to 'cookies.txt' (Recommended - Put file in Brainrot/downloader/)")
        print("  2. Automatically extract cookies from a browser (may fail on Windows Chrome)")
        print("  3. Proceed without cookies")
        method = input("Select option (1-3): ").strip()
        if method == "1":
            print(f"\nPlace 'cookies.txt' in: {local_cookies}")
            input("Press Enter once you have copied 'cookies.txt' to that location...")
            if os.path.exists(local_cookies):
                cookie_file = local_cookies
                print(Fore.GREEN + "cookies.txt loaded successfully.")
            else:
                print(Fore.RED + "File not found. Proceeding without cookies.")
        elif method == "2":
            print("\nSelect browser to extract cookies from:")
            for key, (name, _) in BROWSERS.items():
                print(f"  {key}. {name}")
            b_choice = input("Enter choice (1-5): ").strip()
            if b_choice in BROWSERS:
                browser_name = BROWSERS[b_choice][1]
                print(Fore.GREEN + f"Using cookies from browser: {BROWSERS[b_choice][0]}")

    # Gather URLs per category
    batch_jobs = {}  # folder_name -> [list of urls]
    
    for niche_name, folder in NICHES.items():
        print(Fore.MAGENTA + Style.BRIGHT + f"\n--------------------------------------------------")
        print(Fore.CYAN + Style.BRIGHT + f" NICHE: {niche_name.upper()} (Saves to: {folder})")
        print(Fore.MAGENTA + Style.BRIGHT + f"--------------------------------------------------")
        urls = []
        for i in range(1, 6):
            url = input(f"Enter URL {i}/5 (or press Enter to finish/skip): ").strip()
            if not url:
                break
            urls.append(url)
        if urls:
            batch_jobs[folder] = batch_jobs.get(folder, []) + urls

    if not batch_jobs:
        print(Fore.YELLOW + "\nNo URLs were entered. Exiting downloader.")
        return

    # Find the bundled ffmpeg executable path and make a copy as 'ffmpeg.exe' locally
    bundled_ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    local_ffmpeg = os.path.join(HERE, "ffmpeg.exe")
    if not os.path.exists(local_ffmpeg):
        print(Fore.BLUE + f"\n[FFmpeg] Copying bundled FFmpeg locally as 'ffmpeg.exe'...")
        try:
            shutil.copy(bundled_ffmpeg, local_ffmpeg)
        except Exception as e:
            print(Fore.RED + f"[Warning] Failed to copy FFmpeg locally: {e}")

    # Display Queue Summary
    clear_console()
    print(Fore.MAGENTA + Style.BRIGHT + "==================================================")
    print(Fore.CYAN + Style.BRIGHT + "                DOWNLOAD QUEUE SUMMARY")
    print(Fore.MAGENTA + Style.BRIGHT + "==================================================")
    total_videos = 0
    for folder, urls in batch_jobs.items():
        print(Fore.YELLOW + f"📂 Folder: {folder}")
        for url in urls:
            print(f"   - {url}")
            total_videos += 1
    print(Fore.MAGENTA + Style.BRIGHT + "==================================================")
    print(Fore.GREEN + Style.BRIGHT + f"Total videos queued: {total_videos}")
    confirm = input("Start downloading all videos? (y/n): ").strip().lower()
    if confirm != 'y':
        print(Fore.YELLOW + "Batch download cancelled.")
        return

    # Run Batch Download
    print(Fore.GREEN + "\nStarting batch downloads...\n")
    downloaded_count = 0
    failed_count = 0

    for folder, urls in batch_jobs.items():
        dest_dir = os.path.join(PROJECT_ROOT, folder)
        os.makedirs(dest_dir, exist_ok=True)

        for url in urls:
            downloaded_count += 1
            print(Fore.CYAN + Style.BRIGHT + f"\n[Download {downloaded_count}/{total_videos}] {url} -> {folder}")
            
            ydl_opts = {
                # Format selection: Best video up to 1080p (ideal for vertical mobile shorts & 4x faster downloads)
                "format": "bestvideo[height<=1080]+bestaudio/best",
                "ffmpeg_location": HERE,  # Look in HERE for the copied 'ffmpeg.exe'
                "merge_output_format": "mp4",
                "outtmpl": os.path.join(dest_dir, "%(title)s_1080p.%(ext)s"),
                "concurrent_fragment_downloads": 5,  # Download multiple video fragments in parallel
                # Force enable Node.js as the JS runtime to solve n-challenges
                "js_runtimes": {"node": {}},
                # Only download the first 5 minutes
                "download_sections": [
                    {
                        "section": {
                            "start_time": 0,
                            "end_time": 300,  # 5 minutes in seconds
                        }
                    }
                ],
                "postprocessors": [{
                    "key": "FFmpegVideoConvertor",
                    "preferedformat": "mp4",
                }],
                # Prevent crashing the entire batch if one download fails
                "ignoreerrors": True,
            }

            if cookie_file:
                ydl_opts["cookiefile"] = cookie_file
            elif browser_name:
                ydl_opts["cookiesfrombrowser"] = (browser_name, None, None, None)

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    result = ydl.download([url])
                    # ydl.download returns 0 on success, non-zero on failure
                    if result != 0:
                        failed_count += 1
                        print(Fore.RED + f"❌ Download failed for: {url}")
            except Exception as e:
                failed_count += 1
                print(Fore.RED + f"❌ Exception during download: {e}")

    # Final report
    print(Fore.MAGENTA + Style.BRIGHT + "\n==================================================")
    print(Fore.CYAN + Style.BRIGHT + "                BATCH RUN COMPLETE")
    print(Fore.MAGENTA + Style.BRIGHT + "==================================================")
    print(Fore.GREEN + f"Successfully processed: {total_videos - failed_count}/{total_videos}")
    if failed_count > 0:
        print(Fore.RED + f"Failed: {failed_count}")
    print(Fore.MAGENTA + Style.BRIGHT + "==================================================")

if __name__ == "__main__":
    main()
