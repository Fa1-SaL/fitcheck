import os
import json
import hashlib
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from openai import OpenAI
from utils.helpers import logger
from utils.retries import retry
from config.config import CACHE_DIR, EMBEDDING_MODEL
from models.candidate import Candidate

def get_text_hash(text: str) -> str:
    """Calculate SHA-256 hash of the input text for caching purposes."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def read_from_cache(hash_val: str) -> Optional[Dict]:
    """Read cached JSON file if it exists."""
    cache_path = CACHE_DIR / f"{hash_val}.json"
    if cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None

def write_to_cache(hash_val: str, data: Dict) -> None:
    """Write dictionary to cache as a JSON file."""
    cache_path = CACHE_DIR / f"{hash_val}.json"
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

@retry()
def execute_embeddings_api_call(client: OpenAI, texts: List[str]) -> List[List[float]]:
    """Calls OpenAI Embeddings API with retries."""
    response = client.embeddings.create(
        input=texts,
        model=EMBEDDING_MODEL
    )
    return [item.embedding for item in response.data]

def get_embeddings_batch(texts: List[str], api_key: str) -> List[List[float]]:
    """
    Get a batch of embeddings from OpenAI.
    Checks and writes to cache for each text individually to maximize reusability.
    """
    if not texts:
        return []
        
    client = OpenAI(api_key=api_key)
    
    # Calculate hashes and check caches
    hashes = [get_text_hash(t) + "_embedding" for t in texts]
    results = [None] * len(texts)
    miss_indices = []
    miss_texts = []
    
    # Check cache for each text
    for idx, (text, h) in enumerate(zip(texts, hashes)):
        if not text or not text.strip():
            results[idx] = [0.0] * 1536
            continue
            
        cached_val = read_from_cache(h)
        if cached_val and "embedding" in cached_val:
            results[idx] = cached_val["embedding"]
        else:
            miss_indices.append(idx)
            miss_texts.append(text)
            
    # If we have cache misses, fetch them in a batch API call!
    if miss_texts:
        logger.info(f"Embeddings cache miss. Batch requesting {len(miss_texts)} embeddings from OpenAI (model: {EMBEDDING_MODEL})...")
        try:
            fetched_embeddings = execute_embeddings_api_call(client, miss_texts)
            # Cache the newly fetched embeddings and save in results
            for idx, embedding in zip(miss_indices, fetched_embeddings):
                results[idx] = embedding
                h = hashes[idx]
                write_to_cache(h, {"embedding": embedding})
        except Exception as e:
            logger.error(f"Failed to fetch batch embeddings: {str(e)}")
            # Fallback to zero vectors for failed ones
            for idx in miss_indices:
                results[idx] = [0.0] * 1536
                
    return results

def get_embedding(text: str, api_key: str) -> List[float]:
    """Single wrapper around batched embeddings."""
    return get_embeddings_batch([text], api_key)[0]

def cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
    """Calculate the cosine similarity (dot product of normalized vectors)."""
    if not vec1 or not vec2 or len(vec1) != len(vec2):
        return 0.0
    dot_product = sum(a * b for a, b in zip(vec1, vec2))
    return max(0.0, min(1.0, dot_product)) # Clamp between 0.0 and 1.0

@retry()
def execute_skills_normalization_call(client: OpenAI, system_prompt: str, user_prompt: str) -> str:
    """Helper method to normalized skills via Chat completions API with retries."""
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        response_format={"type": "json_object"},
        temperature=0.1
    )
    return response.choices[0].message.content

def normalize_skills(candidate_skills: List[str], jd_skills: List[str], api_key: str) -> Dict[str, str]:
    """
    Map candidate skill strings to official Job Description skill strings.
    Uses fast local matching first, then falls back to OpenAI and caches results.
    """
    if not candidate_skills or not jd_skills:
        return {}
        
    candidate_skills = [str(s).strip() for s in candidate_skills if str(s).strip()]
    jd_skills = [str(s).strip() for s in jd_skills if str(s).strip()]
    
    if not candidate_skills or not jd_skills:
        return {}
        
    import re
    mapping = {}
    unmatched_jd = []
    cand_clean_map = {re.sub(r'[\W_]+', '', s).lower(): s for s in candidate_skills}
    cand_lower_map = {s.lower(): s for s in candidate_skills}

    for j_skill in jd_skills:
        j_lower = j_skill.lower()
        j_clean = re.sub(r'[\W_]+', '', j_skill).lower()
        # Exact lower match
        if j_lower in cand_lower_map:
            mapping[cand_lower_map[j_lower]] = j_skill
            continue
        # Cleaned alphanumeric match
        if j_clean in cand_clean_map:
            mapping[cand_clean_map[j_clean]] = j_skill
            continue
        # Substring match
        matched = False
        for c_skill in candidate_skills:
            c_lower = c_skill.lower()
            if j_lower in c_lower or c_lower in j_lower:
                if len(c_lower) > 2 and len(j_lower) > 2:
                    mapping[c_skill] = j_skill
                    matched = True
                    break
        if not matched:
            unmatched_jd.append(j_skill)
            
    if not unmatched_jd:
        return mapping
        
    skills_key = f"{','.join(sorted(candidate_skills))}||{','.join(sorted(unmatched_jd))}_skills_mapping"
    hash_val = get_text_hash(skills_key)
    
    # Check Cache for remaining unmatched skills
    cached_mapping = read_from_cache(hash_val)
    if cached_mapping is not None:
        mapping.update(cached_mapping)
        return mapping
        
    # Query LLM to normalize remaining unmapped skills with retry support
    logger.info(f"Skills mapping cache miss for {len(unmatched_jd)} skills. Normalizing via OpenAI...")
    client = OpenAI(api_key=api_key)
    
    system_prompt = (
        "You are an expert recruitment parser. Map a list of candidate skills to an official list of job requirements.\n"
        "Ignore formatting, spelling, capitalization variations, and minor synonyms (e.g. 'ReactJS' maps to 'React', 'Git Version Control' maps to 'Git').\n"
        "Return ONLY a JSON dictionary where keys are candidate skills and values are the matching official skills from the Job Description.\n"
        "If a candidate skill does not map to any job description skill, do not include it in the returned dictionary.\n"
        "Do not include markdown code block formatting."
    )
    
    user_prompt = f"Candidate Skills: {candidate_skills}\nOfficial JD Requirements: {unmatched_jd}"
    
    try:
        raw_content = execute_skills_normalization_call(client, system_prompt, user_prompt)
        llm_mapping = json.loads(raw_content)
        write_to_cache(hash_val, llm_mapping)
        mapping.update(llm_mapping)
        return mapping
    except Exception as e:
        logger.error(f"OpenAI skills normalization failed: {str(e)}")
        return mapping

def calculate_match_score(
    candidate: Candidate, 
    jd_json: Dict, 
    jd_raw_text: str,
    weights: Dict[str, float], 
    api_key: str,
    jd_emb: Optional[List[float]] = None,
    res_emb: Optional[List[float]] = None,
    jd_title_emb: Optional[List[float]] = None,
    cand_title_emb: Optional[List[float]] = None,
    jd_edu_emb: Optional[List[float]] = None,
    cand_edu_emb: Optional[List[float]] = None
) -> Tuple[float, Dict[str, float]]:
    """
    Deterministically computes a matching score out of 100.
    Integrates embeddings and skill mappings from OpenAI.
    Optionally accepts pre-computed embeddings to avoid recalculations.
    """
    if not candidate.structured_resume_json:
        return 0.0, {
            "Required Skills": 0.0,
            "Preferred Skills": 0.0,
            "Semantic Similarity": 0.0,
            "Experience": 0.0,
            "Title Match": 0.0,
            "Edu/Cert Match": 0.0
        }
        
    res_json = candidate.structured_resume_json
    
    # 1. Required Skills Match (Default 50%)
    req_jd_skills = jd_json.get("required_skills", [])
    cand_skills = res_json.get("skills", [])
    
    if not req_jd_skills:
        req_score = 1.0
    else:
        mapping = normalize_skills(cand_skills, req_jd_skills, api_key)
        mapped_jd_skills = set(mapping.values())
        matched_required = [s for s in req_jd_skills if s in mapped_jd_skills]
        req_score = len(matched_required) / len(req_jd_skills)
        
    # 2. Preferred Skills Match (Default 10%)
    pref_jd_skills = jd_json.get("preferred_skills", [])
    if not pref_jd_skills:
        pref_score = 1.0
    else:
        mapping = normalize_skills(cand_skills, pref_jd_skills, api_key)
        mapped_jd_skills = set(mapping.values())
        matched_preferred = [s for s in pref_jd_skills if s in mapped_jd_skills]
        pref_score = len(matched_preferred) / len(pref_jd_skills)
        
    # 3. Semantic Similarity (Default 20%)
    # Use precomputed embeddings if available, otherwise fetch
    if jd_emb is None:
        jd_emb = get_embedding(jd_raw_text, api_key)
    if res_emb is None:
        res_emb = get_embedding(candidate.extracted_text, api_key)
        
    semantic_similarity = cosine_similarity(jd_emb, res_emb)
    # Cosine similarities for resumes are usually in range [0.35, 0.90]. Normalize:
    sem_score = (semantic_similarity - 0.35) / (0.80 - 0.35)
    sem_score = max(0.0, min(1.0, sem_score))
    
    # 4. Experience Match (Default 10%)
    jd_req_exp_years = float(jd_json.get("min_years_experience", 0.0))
    cand_exp_val = res_json.get("years_experience")
    
    try:
        cand_exp_years = float(cand_exp_val) if cand_exp_val is not None else 0.0
    except (ValueError, TypeError):
        cand_exp_years = 0.0
        
    if jd_req_exp_years <= 0:
        exp_score = 1.0
    else:
        if cand_exp_years >= jd_req_exp_years:
            exp_score = 1.0
        else:
            exp_score = cand_exp_years / jd_req_exp_years
            
    # 5. Title Match (Default 5%)
    jd_title = jd_json.get("job_title", "")
    cand_title = res_json.get("current_title", "")
    
    if not jd_title:
        title_score = 1.0
    elif not cand_title or str(cand_title).strip().lower() in ["not specified", "n/a", "unknown"]:
        title_score = 0.0
    else:
        if jd_title_emb is None:
            jd_title_emb = get_embedding(jd_title, api_key)
        if cand_title_emb is None:
            cand_title_emb = get_embedding(cand_title, api_key)
        title_similarity = cosine_similarity(jd_title_emb, cand_title_emb)
        # Cosine similarity for short phrases is higher, normalize range [0.4, 0.9] -> [0, 1]
        title_score = (title_similarity - 0.40) / (0.90 - 0.40)
        title_score = max(0.0, min(1.0, title_score))
        
    # 6. Education / Certification Match (Default 5%)
    jd_edu_list = jd_json.get("education", [])
    cand_edu_list = res_json.get("education", [])
    
    if not jd_edu_list:
        edu_score = 1.0
    elif not cand_edu_list:
        edu_score = 0.0
    else:
        if jd_edu_emb is None:
            jd_edu_str = ", ".join(jd_edu_list)
            jd_edu_emb = get_embedding(jd_edu_str, api_key)
        if cand_edu_emb is None:
            cand_edu_str = ", ".join(cand_edu_list)
            cand_edu_emb = get_embedding(cand_edu_str, api_key)
        edu_similarity = cosine_similarity(jd_edu_emb, cand_edu_emb)
        edu_score = (edu_similarity - 0.35) / (0.85 - 0.35)
        edu_score = max(0.0, min(1.0, edu_score))
        
    # Certifications Match
    jd_certs = jd_json.get("certifications", [])
    cand_certs = res_json.get("certifications", [])
    
    if not jd_certs:
        cert_score = 1.0
    elif not cand_certs:
        cert_score = 0.0
    else:
        matches = 0
        for jd_cert in jd_certs:
            for cand_cert in cand_certs:
                if str(jd_cert).strip().lower() in str(cand_cert).strip().lower():
                    matches += 1
                    break
        cert_score = matches / len(jd_certs)
        
    edu_cert_score = 0.5 * edu_score + 0.5 * cert_score
    
    # 7. Weighted combination
    total_weight = sum(weights.values())
    if total_weight <= 0:
        total_weight = 1.0
        
    w_req = weights.get("required_skills", 0.50) / total_weight
    w_pref = weights.get("preferred_skills", 0.10) / total_weight
    w_sem = weights.get("semantic_similarity", 0.20) / total_weight
    w_exp = weights.get("experience", 0.10) / total_weight
    w_title = weights.get("title", 0.05) / total_weight
    w_edu_cert = weights.get("education_certification", 0.05) / total_weight
    
    final_score_raw = (
        w_req * req_score +
        w_pref * pref_score +
        w_sem * sem_score +
        w_exp * exp_score +
        w_title * title_score +
        w_edu_cert * edu_cert_score
    )
    
    final_score = round(final_score_raw * 100, 1)
    
    sub_scores_dict = {
        "Required Skills": round(req_score * 100, 1),
        "Preferred Skills": round(pref_score * 100, 1),
        "Semantic Similarity": round(sem_score * 100, 1),
        "Experience": round(exp_score * 100, 1),
        "Title Match": round(title_score * 100, 1),
        "Edu/Cert Match": round(edu_cert_score * 100, 1)
    }
    
    return final_score, sub_scores_dict

from config.config import OPENAI_MODEL

@retry()
def execute_recruiter_evaluation_call(client: OpenAI, system_prompt: str, user_prompt: str) -> str:
    """Calls OpenAI Chat API for recruiter resume evaluation with retries."""
    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        response_format={"type": "json_object"},
        temperature=0.1
    )
    return response.choices[0].message.content

def evaluate_candidate_recruiter(
    candidate: Candidate,
    jd_raw_text: str,
    threshold: float,
    api_key: str
) -> Dict:
    """
    Evaluate candidate holistically based on the recruiter system prompt PDF guidelines.
    Caches the evaluation results.
    """
    if not candidate.extracted_text or not candidate.extracted_text.strip():
        return {
            "overall_relevance_score": 0.0,
            "decision": "Rejected",
            "reason": "Resume text is empty or could not be parsed."
        }
        
    eval_key = f"{jd_raw_text}||{candidate.extracted_text}||{threshold}_recruiter_evaluation"
    hash_val = get_text_hash(eval_key)
    
    # Check Cache
    cached_val = read_from_cache(hash_val)
    if cached_val is not None:
        return cached_val
        
    logger.info(f"Recruiter evaluation cache miss for candidate: {candidate.name}. Querying OpenAI...")
    client = OpenAI(api_key=api_key)
    
    system_prompt = (
        "Role\n"
        "You are an experienced senior recruiter with expertise in evaluating candidates across multiple professional domains, "
        "including but not limited to Software Engineering, Data Science, Artificial Intelligence, Design, Photography, Finance, Healthcare, "
        "Legal, Language Services, Business Operations, Engineering, and other professional industries.\n"
        "You are NOT an Applicant Tracking System (ATS).\n"
        "You do NOT perform keyword matching.\n"
        "You do NOT rank resumes based on formatting or keyword density.\n"
        "You evaluate candidates exactly as an experienced recruiter would when manually reviewing applications.\n\n"
        "Objective\n"
        "Your objective is NOT to identify the perfect candidate.\n"
        "Your objective is to eliminate candidates who are clearly irrelevant while identifying candidates who have a realistic chance of succeeding in the role.\n"
        "The candidates have already applied for this specific role, meaning they have already self-selected into the relevant domain.\n"
        "Therefore:\n"
        "• Do not expect perfect matches.\n"
        "• Do not reject candidates simply because they are missing a few skills.\n"
        "• Use recruiter judgment.\n"
        "• Use semantic understanding.\n"
        "• Use contextual reasoning.\n"
        "• Focus on whether the candidate deserves to move to the next stage.\n"
        "Think like a recruiter asking: \"Would I confidently move this candidate to the next stage of the hiring process?\"\n\n"
        "Job Description Analysis\n"
        "Before reading the resume, carefully analyze the Job Description.\n"
        "Understand: Role, Domain, Responsibilities, Required skills, Required tools, Required technologies, Required software, Required certifications, Required licenses, Required education (only if important), Portfolio requirements (if applicable).\n"
        "Then classify every requirement into one of three categories:\n"
        "1. Mandatory Requirements:\n"
        "Requirements explicitly described using words such as: Must, Must Have, Mandatory, Required, Essential.\n"
        "These represent the highest priority. Missing mandatory requirements should significantly reduce the score. However, use recruiter reasoning. "
        "If the resume demonstrates equivalent or transferable expertise that clearly satisfies the intent of the requirement, consider that evidence instead of relying on exact wording.\n"
        "Years of experience are NEVER mandatory, even if the Job Description says \"5+ years required\" or similar wording. Treat years of experience only as supporting evidence.\n"
        "2. Important Requirements:\n"
        "Requirements that strongly influence success but are not explicitly mandatory (e.g. Strong experience with, Proficiency in, Expertise in, Experience with, Knowledge of, Familiarity with). These should heavily influence the evaluation.\n"
        "3. Preferred Requirements:\n"
        "Requirements described using phrases such as: Preferred, Nice to have, Bonus, Good to have, Exposure to.\n"
        "Years of experience always belong in this category. Regardless of how many years the Job Description requests, experience should never automatically reject or qualify a candidate.\n\n"
        "Resume Evaluation\n"
        "Read the entire resume. Evaluate the candidate holistically. Do NOT perform keyword matching. Instead understand: Skills, Technical capabilities, Functional capabilities, Projects, Responsibilities, Achievements, Domain expertise, Certifications, Licenses, Portfolio, Transferable knowledge. Use semantic reasoning and understand related/transferable technologies (e.g., Django/Flask/FastAPI/LangChain/RAG count as Python; DaVinci Resolve/Final Cut Pro count as Premiere Pro).\n\n"
        "Recruiter Thinking\n"
        "Think exactly like an experienced recruiter. Ask yourself:\n"
        "Can this candidate realistically perform this role? Would I move this candidate to the next stage? Would I feel comfortable sending this candidate to the client?\n"
        "If yes, the candidate should receive a high score. If not, the score should be lower. Do NOT search for perfection. Search for suitability.\n\n"
        "Experience Evaluation\n"
        "Years of experience should NEVER be treated as mandatory. Do NOT reject candidates because they have fewer years than requested. Experience should simply provide additional confidence. A candidate with two years of highly relevant work may be a stronger fit than someone with ten years of unrelated experience. Capability is more important than duration.\n\n"
        "Resume Formatting\n"
        "Ignore: Resume design, fonts, colors, templates, ATS optimization, keyword stuffing. Only evaluate the candidate.\n\n"
        "Scoring Philosophy\n"
        "Assign one Overall Relevance Score from 0–100 representing your confidence that the candidate can successfully perform the role.\n"
        "Guidelines:\n"
        "• 90–100: Outstanding fit\n"
        "• 80–89: Strong fit\n"
        "• 70–79: Good fit\n"
        "• 60–69: Relevant enough to move forward\n"
        "• Below 60: Not suitable for this role\n"
        "These ranges are guidance, not rigid rules. Always use recruiter judgment.\n\n"
        "Decision\n"
        "If the Overall Relevance Score is greater than or equal to the threshold provided by the user, Decision = Shortlisted. Otherwise, Decision = Rejected.\n\n"
        "Consistency\n"
        "Candidates with similar resumes should receive similar scores. Avoid random scoring. Be objective. Be consistent.\n\n"
        "Output Format\n"
        "Return ONLY valid JSON. Do not include markdown code blocks. Do not include explanations outside the JSON.\n"
        "Expected JSON Output Structure:\n"
        "{\n"
        "  \"overall_relevance_score\": 0,\n"
        "  \"decision\": \"Shortlisted or Rejected\",\n"
        "  \"reason\": \"Briefly explain why the candidate received this score. Mention mandatory requirements, overall strengths, transferable skills if applicable, and any major gaps that affected the evaluation.\"\n"
        "}"
    )
    
    user_prompt = (
        f"Job Description:\n{jd_raw_text}\n\n"
        f"Candidate Resume:\n{candidate.extracted_text}\n\n"
        f"Shortlist Score Threshold: {threshold}"
    )
    
    try:
        raw_content = execute_recruiter_evaluation_call(client, system_prompt, user_prompt)
        res_json = json.loads(raw_content)
        
        # Validate keys and ensure types are correct
        score = res_json.get("overall_relevance_score", 0.0)
        try:
            res_json["overall_relevance_score"] = float(score)
        except (ValueError, TypeError):
            res_json["overall_relevance_score"] = 0.0
            
        write_to_cache(hash_val, res_json)
        return res_json
    except Exception as e:
        logger.error(f"OpenAI recruiter evaluation failed for candidate {candidate.name}: {str(e)}")
        # Return fallback evaluation
        return {
            "overall_relevance_score": 0.0,
            "decision": "Rejected",
            "reason": f"AI recruiter evaluation failed: {str(e)}"
        }
