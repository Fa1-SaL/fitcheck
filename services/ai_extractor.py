import os
import json
import hashlib
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from openai import OpenAI
from utils.helpers import logger
from utils.retries import retry
from config.config import CACHE_DIR, OPENAI_MODEL

def get_text_hash(text: str) -> str:
    """Calculate SHA-256 hash of the input text for caching purposes."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def read_from_cache(hash_val: str) -> Optional[Dict]:
    """Read cached JSON file if it exists."""
    cache_path = CACHE_DIR / f"{hash_val}.json"
    if cache_path.exists():
        logger.info(f"Cache hit for AI Extraction: {hash_val[:12]}")
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to read cache file {cache_path}: {str(e)}")
    return None

def write_to_cache(hash_val: str, data: Dict) -> None:
    """Write dictionary to cache as a JSON file."""
    cache_path = CACHE_DIR / f"{hash_val}.json"
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info(f"Saved extraction to cache: {cache_path.name}")
    except Exception as e:
        logger.warning(f"Failed to write to cache {cache_path}: {str(e)}")

def validate_resume_json(data: Dict) -> List[str]:
    """
    Validate the structured resume JSON data.
    Returns a list of warning strings for missing or placeholder critical fields.
    """
    warnings = []
    
    # 1. Candidate Name Validation
    name = data.get("candidate_name", "")
    if not name or str(name).strip().lower() in ["", "not specified", "n/a", "not found", "unknown"]:
        warnings.append("Missing Candidate Name")
        
    # 2. Skills Validation
    skills = data.get("skills", [])
    if not skills or not isinstance(skills, list) or len(skills) == 0:
        warnings.append("Missing Skills")
        
    # 3. Experience Validation
    years_exp = data.get("years_experience")
    if years_exp is None or str(years_exp).strip().lower() in ["", "not specified", "n/a", "unknown"]:
        warnings.append("Missing Years of Experience")
    else:
        try:
            val = float(years_exp)
            if val < 0:
                warnings.append("Invalid Experience (Negative value)")
        except (ValueError, TypeError):
            if not str(years_exp).strip():
                warnings.append("Missing Years of Experience")

    # 4. Education Validation
    edu = data.get("education", [])
    if not edu or not isinstance(edu, list) or len([e for e in edu if str(e).strip()]):
        non_empty_edu = [e for e in edu if str(e).strip()]
        if not non_empty_edu:
            warnings.append("Missing Education history")
            
    # 5. Companies Validation
    companies = data.get("companies", [])
    if not companies or not isinstance(companies, list) or len([c for c in companies if str(c).strip()]) == 0:
        warnings.append("Missing Company history")
        
    return warnings

@retry()
def execute_openai_jd_call(client: OpenAI, system_prompt: str, jd_text: str) -> str:
    """Helper method to make JD Chat API call with retries."""
    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Extract details from this Job Description:\n\n{jd_text}"}
        ],
        response_format={"type": "json_object"},
        temperature=0.1
    )
    return response.choices[0].message.content

def extract_job_description(jd_text: str, api_key: str) -> Dict:
    """
    Use OpenAI Chat API to extract structured JSON from a Job Description.
    """
    if not api_key:
        raise ValueError("OpenAI API Key is required for Job Description extraction.")
        
    logger.info(f"Extracting Job Description using model: {OPENAI_MODEL}...")
    client = OpenAI(api_key=api_key)
    
    system_prompt = (
        "You are an expert recruiter AI system. Analyze the provided Job Description text and extract structured fields in JSON format. "
        "Return ONLY a JSON object. Do not include markdown code block formatting (like ```json). Use the exact keys specified below:\n\n"
        "Expected JSON Schema:\n"
        "{\n"
        "  \"job_title\": \"Official Job Title (string)\",\n"
        "  \"required_skills\": [\"Skill 1\", \"Skill 2\", ...],\n"
        "  \"preferred_skills\": [\"Preferred Skill 1\", \"Preferred Skill 2\", ...],\n"
        "  \"required_experience\": \"Minimum experience requirements text description (string)\",\n"
        "  \"min_years_experience\": Minimum number of years of experience required as an integer or float, e.g. 5 or 0 if not specified (numeric),\n"
        "  \"education\": [\"Required degree/field of study\", ...],\n"
        "  \"certifications\": [\"Required/preferred certifications\", ...],\n"
        "  \"employment_type\": \"Full-time, Part-time, Contract, etc. (string)\",\n"
        "  \"industry\": \"Industry sector, e.g. Software, Finance, Healthcare (string)\",\n"
        "  \"seniority\": \"Junior, Mid, Senior, Lead, Executive (string)\"\n"
        "}"
    )
    
    try:
        raw_content = execute_openai_jd_call(client, system_prompt, jd_text)
        result_json = json.loads(raw_content)
        
        try:
            result_json["min_years_experience"] = float(result_json.get("min_years_experience", 0))
        except (ValueError, TypeError):
            result_json["min_years_experience"] = 0.0
            
        logger.info("Successfully extracted structured Job Description JSON.")
        return result_json
    except Exception as e:
        logger.error(f"OpenAI Job Description extraction failed: {str(e)}")
        return {
            "job_title": "Unknown",
            "required_skills": [],
            "preferred_skills": [],
            "required_experience": "Unknown",
            "min_years_experience": 0.0,
            "education": [],
            "certifications": [],
            "employment_type": "Unknown",
            "industry": "Unknown",
            "seniority": "Unknown",
            "error": str(e)
        }

@retry()
def execute_openai_resume_call(client: OpenAI, system_prompt: str, resume_text: str) -> str:
    """Helper method to make Resume Chat API call with retries."""
    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Extract details from this Resume:\n\n{resume_text}"}
        ],
        response_format={"type": "json_object"},
        temperature=0.1
    )
    return response.choices[0].message.content

def extract_resume_info(resume_text: str, api_key: str) -> Tuple[Dict, List[str]]:
    """
    Use OpenAI Chat API to extract structured JSON from a Resume.
    Caches the extraction based on the SHA-256 hash of the resume text.
    """
    if not resume_text or not resume_text.strip():
        return {"error": "Empty resume text"}, ["Empty raw text"]
        
    hash_val = get_text_hash(resume_text)
    
    # 1. Check Cache
    cached_data = read_from_cache(hash_val)
    if cached_data is not None:
        warnings = validate_resume_json(cached_data)
        return cached_data, warnings
        
    # 2. Cache Miss - Call OpenAI
    if not api_key:
        raise ValueError("OpenAI API Key is required for Resume extraction.")
        
    logger.info(f"AI cache miss. Call OpenAI to structure resume (model: {OPENAI_MODEL})...")
    client = OpenAI(api_key=api_key)
    
    system_prompt = (
        "You are an expert recruiter AI system. Analyze the provided resume text and extract candidate profiles into structured JSON. "
        "Return ONLY a JSON object. Do not include markdown code block formatting. Use the exact keys specified below:\n\n"
        "Expected JSON Schema:\n"
        "{\n"
        "  \"candidate_name\": \"Candidate Full Name (string)\",\n"
        "  \"current_title\": \"Current professional title (string)\",\n"
        "  \"skills\": [\"Skill 1\", \"Skill 2\", ...],\n"
        "  \"years_experience\": Number of years of experience as an integer or float, e.g. 5 or 8.5 (numeric or null),\n"
        "  \"education\": [\"Degree - Major (School/University)\", ...],\n"
        "  \"certifications\": [\"Cert Name 1\", ...],\n"
        "  \"companies\": [\"Company Name 1\", \"Company Name 2\", ...],\n"
        "  \"industry_experience\": [\"Sectors/Industries worked in\", ...]\n"
        "}"
    )
    
    try:
        raw_content = execute_openai_resume_call(client, system_prompt, resume_text)
        result_json = json.loads(raw_content)
        
        # 3. Write to Cache
        write_to_cache(hash_val, result_json)
        
        # 4. Validate
        warnings = validate_resume_json(result_json)
        
        return result_json, warnings
    except Exception as e:
        logger.error(f"OpenAI Resume extraction failed: {str(e)}")
        fallback_json = {
            "candidate_name": "Unknown (Failed to parse)",
            "current_title": "Unknown",
            "skills": [],
            "years_experience": None,
            "education": [],
            "certifications": [],
            "companies": [],
            "industry_experience": [],
            "error": str(e)
        }
        return fallback_json, [f"AI extraction failure: {str(e)}"]
