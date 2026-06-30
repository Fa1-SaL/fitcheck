import os
import shutil
import pandas as pd
from pathlib import Path
from typing import List
from models.candidate import Candidate
from utils.helpers import logger
from config.config import SHORTLISTED_DIR, OUTPUT_DIR

def clear_shortlisted_folder() -> None:
    """Clear all files in the shortlisted directory to ensure a fresh output run."""
    logger.info(f"Clearing historical files in: {SHORTLISTED_DIR.name}/")
    SHORTLISTED_DIR.mkdir(parents=True, exist_ok=True)
    
    # Iterate and delete files
    for item in SHORTLISTED_DIR.iterdir():
        try:
            if item.is_file() or item.is_symlink():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item)
        except Exception as e:
            logger.warning(f"Failed to delete {item.name}: {str(e)}")

def get_non_conflicting_path(directory: Path, filename: str) -> Path:
    """Find a path for the filename in the directory, appending a suffix if it already exists."""
    dest = directory / filename
    if not dest.exists():
        return dest
        
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    counter = 1
    while True:
        new_filename = f"{stem}_{counter}{suffix}"
        dest = directory / new_filename
        if not dest.exists():
            return dest
        counter += 1

def download_and_copy_shortlisted(candidates: List[Candidate], dest_dir: Path) -> dict:
    """Manually download or copy all candidate resumes into the destination folder, resolving name conflicts."""
    import time
    import re
    from services.google_drive import download_resume
    
    logger.info(f"Starting manual export of resumes to: {dest_dir}")
    start_time = time.time()
    dest_dir.mkdir(parents=True, exist_ok=True)
    
    downloaded_count = 0
    failed_count = 0
    
    for cand in candidates:
        src_path = None
        
        # Check if cached locally
        if cand.local_path and Path(cand.local_path).exists():
            src_path = Path(cand.local_path)
        elif cand.resume_url:
            # Try downloading from Drive URL
            logger.info(f"Cache miss for candidate {cand.name}. Downloading fresh...")
            try:
                downloaded_path, file_name = download_resume(cand.resume_url, cand.name)
                if downloaded_path and Path(downloaded_path).exists():
                    src_path = Path(downloaded_path)
                    cand.local_path = str(downloaded_path)
                    cand.file_name = file_name
            except Exception as e:
                logger.error(f"Failed to download resume for {cand.name} during manual export: {str(e)}")
        
        if src_path:
            orig_filename = src_path.name
            
            # Strip unique hash prefix if it was appended (e.g. "a1b2c3d4__resume.pdf" -> "resume.pdf")
            clean_name = orig_filename
            if "_" in orig_filename:
                parts = orig_filename.split("_", 1)
                if len(parts) == 2:
                    prefix = parts[0]
                    # Check if prefix is hexadecimal (hash) of length >= 8
                    if all(c in "0123456789abcdefABCDEF" for c in prefix) and len(prefix) >= 8:
                        clean_name = parts[1]
            
            safe_cand_name = re.sub(r'[\\/*?:"<>|]', "", cand.name).strip()
            if safe_cand_name:
                target_name = f"{safe_cand_name} - {clean_name}"
            else:
                target_name = clean_name
            
            dest_path = get_non_conflicting_path(dest_dir, target_name)
            try:
                shutil.copy2(src_path, dest_path)
                downloaded_count += 1
                logger.info(f"Saved resume for {cand.name} to {dest_path.name}")
            except Exception as e:
                logger.error(f"Failed to save resume for {cand.name} to destination: {str(e)}")
                failed_count += 1
        else:
            logger.warning(f"Could not retrieve resume for candidate {cand.name}.")
            failed_count += 1
                
    elapsed = time.time() - start_time
    logger.info(f"Manual export completed. Successfully saved: {downloaded_count}, Failed: {failed_count}")
    return {
        "downloaded": downloaded_count,
        "failed": failed_count,
        "destination": str(dest_dir),
        "time_taken": round(elapsed, 2)
    }

def export_csv_results(candidates: List[Candidate]) -> Path:
    """
    Generate and save results.csv containing Candidate Name, Match Score, Status, Resume URL,
    sorted by Match Score descending.
    """
    logger.info("Exporting results.csv...")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    data = []
    for cand in candidates:
        # Save Match Score as float or 0.0 for sorting, display "N/A" for failures if needed
        # We'll use 0.0 for failed candidates so they sort to the bottom
        score = cand.match_score if cand.match_score is not None else 0.0
        data.append({
            "Candidate Name": cand.name,
            "Match Score": score,
            "Status": cand.status,
            "Resume URL": cand.resume_url
        })
        
    df = pd.DataFrame(data)
    # Sort descending by Match Score
    df = df.sort_values(by="Match Score", ascending=False)
    
    dest_path = OUTPUT_DIR / "results.csv"
    df.to_csv(dest_path, index=False)
    logger.info(f"Saved results CSV: {dest_path.name}")
    return dest_path

def archive_processing_logs(log_history: List[str]) -> Path:
    """Write processing logs into output/processing_log.txt."""
    logger.info("Saving processing logs to file...")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    dest_path = OUTPUT_DIR / "processing_log.txt"
    try:
        with open(dest_path, "w", encoding="utf-8") as f:
            f.write("\n".join(log_history))
        logger.info(f"Saved processing logs: {dest_path.name}")
    except Exception as e:
        logger.error(f"Failed to save log file: {str(e)}")
        
    return dest_path
