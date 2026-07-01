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

def detect_extension_and_rename(dest_path: Path, current_ext: str, content_type: str) -> Path:
    """Detect real file type from magic bytes/content type and fix file extension if needed."""
    try:
        header = dest_path.read_bytes()[:2048]
    except Exception:
        return dest_path
        
    real_ext = current_ext
    header_strip = header.strip()
    
    if header.startswith(b"%PDF"):
        real_ext = ".pdf"
    elif header.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        if current_ext in [".docx", ".odt", ".epub", ".zip"]:
            real_ext = current_ext
        else:
            real_ext = ".docx"
    elif header.startswith(b"\xd0\xcf\x11\xe0"):
        real_ext = ".doc"
    elif header.startswith(b"{\\rtf") or b"{\\rtf" in header[:50]:
        real_ext = ".rtf"
    elif header_strip.startswith((b"<!DOCTYPE html", b"<html", b"<!doctype html")):
        real_ext = ".html"
    else:
        if current_ext not in [".pdf", ".docx", ".doc", ".rtf", ".txt", ".html", ".odt"]:
            if "pdf" in content_type:
                real_ext = ".pdf"
            elif "officedocument" in content_type or "wordprocessingml" in content_type:
                real_ext = ".docx"
            elif "msword" in content_type:
                real_ext = ".doc"
            elif "rtf" in content_type:
                real_ext = ".rtf"
            elif "text/html" in content_type:
                real_ext = ".html"
            elif "text/plain" in content_type:
                real_ext = ".txt"
            else:
                real_ext = ".txt" if not current_ext else current_ext
                
    if real_ext != dest_path.suffix.lower():
        new_path = dest_path.with_suffix(real_ext)
        try:
            if new_path.exists() and new_path != dest_path:
                new_path.unlink()
            dest_path.rename(new_path)
            logger.info(f"Renamed downloaded file from {dest_path.name} to {new_path.name} based on magic bytes.")
            return new_path
        except Exception as e:
            logger.warning(f"Failed to rename file extension to {real_ext}: {e}")
            
    return dest_path

@retry(exceptions=(requests.RequestException, RuntimeError))
def download_via_public_url(file_id: str, link_type: str, candidate_name: str, url_hash: str) -> Tuple[Path, str]:
    """
    Download a public Google Drive file using standard HTTP requests with retries.
    Handles virus confirmation HTML pages and Google Doc exports.
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "*/*"
    })
    safe_name = re.sub(r"[^\w\-_.]", "_", candidate_name)
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    
    if link_type == "document":
        logger.info(f"Downloading Google Doc ID {file_id} via DOCX export link...")
        url = f"https://docs.google.com/document/d/{file_id}/export"
        for fmt, ext in [("docx", ".docx"), ("pdf", ".pdf"), ("txt", ".txt")]:
            response = session.get(url, params={"format": fmt}, stream=True, timeout=20)
            if response.status_code == 200 and not response.headers.get("Content-Type", "").startswith("text/html"):
                filename = f"{url_hash}_{safe_name}_resume{ext}"
                dest_path = DOWNLOADS_DIR / filename
                with open(dest_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=131072):
                        if chunk:
                            f.write(chunk)
                dest_path = detect_extension_and_rename(dest_path, ext, response.headers.get("Content-Type", ""))
                logger.info(f"Saved Google Doc download to: {dest_path.name}")
                return dest_path, dest_path.name
        raise RuntimeError("Failed to export Google Doc in any supported format.")
    else:
        logger.info(f"Downloading binary Drive File ID {file_id}...")
        url = "https://drive.google.com/uc"
        params = {"export": "download", "id": file_id}
        response = session.get(url, params=params, stream=True, timeout=20)
        
        # 1. Check for Google Drive virus scan warning token in cookies
        token = get_confirm_token(response)
        if token:
            logger.info("Handling Google Drive virus confirmation token...")
            params["confirm"] = token
            response = session.get(url, params=params, stream=True, timeout=20)
            
        # 2. Check if response is HTML (virus scan confirmation form or export warning)
        content_type = response.headers.get("Content-Type", "")
        if response.status_code == 200 and "text/html" in content_type:
            # Read response content to check for form or virus warning
            content_bytes = response.content
            html_text = content_bytes.decode("utf-8", errors="ignore")
            
            # Check for form submission action (modern Drive virus warning)
            action_match = re.search(r'action="([^"]+)"', html_text)
            inputs = re.findall(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', html_text)
            if action_match and inputs:
                action_url = action_match.group(1)
                form_params = dict(inputs)
                logger.info("Submitting Google Drive virus scan confirmation form...")
                response = session.get(action_url, params=form_params, stream=True, timeout=20)
                content_type = response.headers.get("Content-Type", "")
            elif "can't scan this file for viruses" in html_text or "confirm=" in html_text:
                # Fallback form submission url
                form_params = {"id": file_id, "export": "download", "confirm": "t"}
                response = session.get("https://drive.usercontent.google.com/download", params=form_params, stream=True, timeout=20)
                content_type = response.headers.get("Content-Type", "")
            elif "Google Docs" in html_text or "export" in html_text:
                # It's actually a Google Doc that was linked as binary ID
                return download_via_public_url(file_id, "document", candidate_name, url_hash)
                
        if response.status_code != 200:
            raise RuntimeError(f"Failed to download Google Drive file. Status code: {response.status_code}")
            
        original_filename = parse_filename_from_headers(response, "resume")
        ext = Path(original_filename).suffix.lower()
        
        # If downloaded content is still HTML, try exporting as Google Doc
        if "text/html" in response.headers.get("Content-Type", ""):
            try:
                return download_via_public_url(file_id, "document", candidate_name, url_hash)
            except Exception:
                pass
                
        filename = f"{url_hash}_{safe_name}_resume{ext if ext else '.docx'}"
        dest_path = DOWNLOADS_DIR / filename
        
        with open(dest_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=131072):
                if chunk:
                    f.write(chunk)
                    
        dest_path = detect_extension_and_rename(dest_path, ext, response.headers.get("Content-Type", ""))
        logger.info(f"Saved download to: {dest_path.name}")
        return dest_path, dest_path.name

@retry(exceptions=(requests.RequestException, RuntimeError))
def download_direct_link(url: str, candidate_name: str, url_hash: str) -> Tuple[Path, str]:
    """Download a direct URL (non-Google Drive) using standard HTTP requests with retries."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "*/*"
    })
    safe_name = re.sub(r"[^\w\-_.]", "_", candidate_name)
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(f"Downloading direct link URL for candidate {candidate_name}...")
    
    response = session.get(url, stream=True, timeout=20)
    if response.status_code != 200:
        raise RuntimeError(f"Direct download failed. Status code: {response.status_code}")
        
    original_filename = parse_filename_from_headers(response, "resume")
    ext = Path(original_filename).suffix.lower()
    content_type = response.headers.get("Content-Type", "")
            
    filename = f"{url_hash}_{safe_name}_resume{ext if ext else '.docx'}"
    dest_path = DOWNLOADS_DIR / filename
    
    with open(dest_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=131072):
            if chunk:
                f.write(chunk)
                
    dest_path = detect_extension_and_rename(dest_path, ext, content_type)
    logger.info(f"Saved download to: {dest_path.name}")
    return dest_path, dest_path.name

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
