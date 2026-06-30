import re
import os
import requests
import hashlib
from pathlib import Path
from typing import Tuple, Optional
from utils.helpers import logger
from utils.retries import retry
from config.config import DOWNLOADS_DIR

def parse_google_drive_link(url: str) -> Tuple[Optional[str], str]:
    """
    Parse Google Drive link to extract the File ID and determine if it's a Google Doc.
    """
    url = url.strip()
    
    # 1. Matches docs.google.com/document/d/<ID>/...
    gdoc_match = re.search(r"docs\.google\.com/document/d/([a-zA-Z0-9-_]+)", url)
    if gdoc_match:
        return gdoc_match.group(1), "document"
        
    # 2. Matches drive.google.com/file/d/<ID>/...
    file_match = re.search(r"drive\.google\.com/file/d/([a-zA-Z0-9-_]+)", url)
    if file_match:
        return file_match.group(1), "binary"
        
    # 3. Matches drive.google.com/open?id=<ID>
    open_match = re.search(r"id=([a-zA-Z0-9-_]+)", url)
    if open_match:
        if "document" in url:
            return open_match.group(1), "document"
        return open_match.group(1), "binary"
        
    # 4. If it looks like just an ID
    if re.match(r"^[a-zA-Z0-9-_]{15,60}$", url):
        return url, "binary"
        
    return None, "unknown"

def get_confirm_token(response: requests.Response) -> Optional[str]:
    """Retrieve Google Drive download warning confirmation token from cookies."""
    for key, value in response.cookies.items():
        if key.startswith("download_warning"):
            return value
    return None

def parse_filename_from_headers(response: requests.Response, fallback_name: str) -> str:
    """Parse filename from Content-Disposition header, or use fallback name."""
    cd = response.headers.get("Content-Disposition")
    if not cd:
        return fallback_name
        
    fname_match = re.search(r'filename="([^"]+)"', cd)
    if fname_match:
        return fname_match.group(1)
        
    fname_match_raw = re.search(r'filename=([^;]+)', cd)
    if fname_match_raw:
        return fname_match_raw.group(1).strip()
        
    return fallback_name

@retry(exceptions=(requests.RequestException, RuntimeError))
def download_via_public_url(file_id: str, link_type: str, candidate_name: str, url_hash: str) -> Tuple[Path, str]:
    """
    Download a public Google Drive file using standard HTTP requests with retries.
    Saves the file to DOWNLOADS_DIR with a hashed prefix.
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "*/*"
    })
    safe_name = re.sub(r"[^\w\-_.]", "_", candidate_name)
    
    if link_type == "document":
        logger.info(f"Downloading Google Doc ID {file_id} via PDF export link...")
        url = f"https://docs.google.com/document/d/{file_id}/export"
        params = {"format": "pdf"}
        response = session.get(url, params=params, stream=True, timeout=20)
        
        if response.status_code != 200:
            raise RuntimeError(f"Failed to export Google Doc. Status code: {response.status_code}")
            
        filename = f"{url_hash}_{safe_name}_resume.pdf"
    else:
        logger.info(f"Downloading binary Drive File ID {file_id}...")
        url = "https://drive.google.com/uc"
        params = {"export": "download", "id": file_id}
        response = session.get(url, params=params, stream=True, timeout=20)
        
        # Check for Google Drive virus scan warning
        token = get_confirm_token(response)
        if token:
            logger.info("Handling Google Drive virus confirmation token...")
            params["confirm"] = token
            response = session.get(url, params=params, stream=True, timeout=20)
            
        if response.status_code != 200:
            raise RuntimeError(f"Failed to download Google Drive file. Status code: {response.status_code}")
            
        original_filename = parse_filename_from_headers(response, "resume")
        ext = Path(original_filename).suffix.lower()
        
        if ext not in [".pdf", ".docx", ".doc"]:
            content_type = response.headers.get("Content-Type", "")
            if "pdf" in content_type:
                ext = ".pdf"
            elif "officedocument" in content_type:
                ext = ".docx"
            elif "msword" in content_type:
                ext = ".doc"
            else:
                ext = ".pdf" # default
                
        filename = f"{url_hash}_{safe_name}_resume{ext}"
                
    dest_path = DOWNLOADS_DIR / filename
    
    # Save the file with 128KB chunks for fast disk writing
    with open(dest_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=131072):
            if chunk:
                f.write(chunk)
                
    logger.info(f"Saved download to: {dest_path.name}")
    return dest_path, filename

@retry(exceptions=(requests.RequestException, RuntimeError))
def download_direct_link(url: str, candidate_name: str, url_hash: str) -> Tuple[Path, str]:
    """Download a direct URL (non-Google Drive) using standard HTTP requests with retries."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "*/*"
    })
    safe_name = re.sub(r"[^\w\-_.]", "_", candidate_name)
    logger.info(f"Downloading direct link URL for candidate {candidate_name}...")
    
    response = session.get(url, stream=True, timeout=20)
    if response.status_code != 200:
        raise RuntimeError(f"Direct download failed. Status code: {response.status_code}")
        
    original_filename = parse_filename_from_headers(response, "resume")
    ext = Path(original_filename).suffix.lower()
    
    if ext not in [".pdf", ".docx", ".doc"]:
        content_type = response.headers.get("Content-Type", "")
        if "pdf" in content_type:
            ext = ".pdf"
        elif "officedocument" in content_type:
            ext = ".docx"
        elif "msword" in content_type:
            ext = ".doc"
        else:
            ext = ".pdf"
            
    filename = f"{url_hash}_{safe_name}_resume{ext}"
    dest_path = DOWNLOADS_DIR / filename
    
    with open(dest_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=131072):
            if chunk:
                f.write(chunk)
                
    logger.info(f"Saved download to: {dest_path.name}")
    return dest_path, filename

def download_resume(url: str, candidate_name: str) -> Tuple[Path, str]:
    """
    Orchestrate resume download. Checks local folder cache first.
    Reuses existing downloaded files mapping to the URL hash.
    
    Returns:
        Tuple[Path, str]: (absolute_file_path, filename)
    """
    url = url.strip()
    # Calculate unique hash from the URL
    url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    
    # Check if a file matching this url_hash already exists in the downloads directory
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    for item in DOWNLOADS_DIR.iterdir():
        if item.is_file() and item.name.startswith(f"{url_hash}_"):
            logger.info(f"Reused downloaded resume for {candidate_name} (found cached: {item.name})")
            return item, item.name
            
    # Check link type
    file_id, link_type = parse_google_drive_link(url)
    if not file_id:
        # Fallback to direct URL download
        return download_direct_link(url, candidate_name, url_hash)
        
    # Cache miss - download it with retry support
    return download_via_public_url(file_id, link_type, candidate_name, url_hash)
