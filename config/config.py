import os
import json
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Base directories
BASE_DIR = Path(__file__).resolve().parent.parent

# Default configuration settings
DEFAULT_SETTINGS = {
    "openai_model": "gpt-4o-mini",
    "embedding_model": "text-embedding-3-small",
    "default_shortlist_threshold": 75,
    "scoring_weights": {
        "required_skills": 50.0,
        "preferred_skills": 10.0,
        "semantic_similarity": 20.0,
        "experience": 10.0,
        "title": 5.0,
        "education_certification": 5.0
    },
    "cache_location": "cache",
    "downloads_folder": "downloads",
    "output_folder": "output",
    "shortlisted_folder": "shortlisted",
    "max_concurrency": 4,
    "max_retries": 3,
    "retry_backoff_factor": 2.0
}

# Settings file path
SETTINGS_FILE = BASE_DIR / "config" / "settings.json"

def load_settings() -> dict:
    """Load settings from settings.json or write and load defaults if missing."""
    if not SETTINGS_FILE.parent.exists():
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                # Merge loaded settings with defaults to ensure all keys exist
                merged = DEFAULT_SETTINGS.copy()
                merged.update(loaded)
                return merged
        except Exception as e:
            print(f"Error reading settings.json, reverting to defaults. Details: {str(e)}")
            
    # Write defaults if missing or error occurs
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_SETTINGS, f, indent=2)
    except Exception:
        pass
    return DEFAULT_SETTINGS

# Load active settings
app_settings = load_settings()

# Path declarations (resolved relative to BASE_DIR if not absolute)
def resolve_dir_path(key_name: str) -> Path:
    val = app_settings.get(key_name)
    path = Path(val) if val else Path(DEFAULT_SETTINGS[key_name])
    if not path.is_absolute():
        return (BASE_DIR / path).resolve()
    return path.resolve()

DOWNLOADS_DIR = resolve_dir_path("downloads_folder")
OUTPUT_DIR = resolve_dir_path("output_folder")
SHORTLISTED_DIR = resolve_dir_path("shortlisted_folder")
CACHE_DIR = resolve_dir_path("cache_location")

# Ensure all directories exist
for directory in [DOWNLOADS_DIR, OUTPUT_DIR, SHORTLISTED_DIR, CACHE_DIR]:
    directory.mkdir(parents=True, exist_ok=True)

# Export Settings Variables
OPENAI_MODEL = app_settings.get("openai_model", "gpt-4o-mini")
EMBEDDING_MODEL = app_settings.get("embedding_model", "text-embedding-3-small")
DEFAULT_SHORTLIST_THRESHOLD = app_settings.get("default_shortlist_threshold", 75)
DEFAULT_SCORING_WEIGHTS = app_settings.get("scoring_weights", DEFAULT_SETTINGS["scoring_weights"])
MAX_CONCURRENCY = int(app_settings.get("max_concurrency", 4))
MAX_RETRIES = int(app_settings.get("max_retries", 3))
RETRY_BACKOFF_FACTOR = float(app_settings.get("retry_backoff_factor", 2.0))
