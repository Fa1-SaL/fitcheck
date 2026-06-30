import time
import functools
from utils.helpers import logger
from config.config import MAX_RETRIES, RETRY_BACKOFF_FACTOR

def retry(max_retries=None, backoff_factor=None, exceptions=(Exception,)):
    """
    Decorator that retries the wrapped function with exponential backoff on exceptions.
    """
    # Load defaults from config if not specified
    max_retries_val = max_retries if max_retries is not None else MAX_RETRIES
    backoff_factor_val = backoff_factor if backoff_factor is not None else RETRY_BACKOFF_FACTOR
    
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            retries = 0
            delay = 1.0  # Start with 1 second delay
            while retries <= max_retries_val:
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    retries += 1
                    if retries > max_retries_val:
                        logger.error(f"Function {func.__name__} failed after {max_retries_val} retries. Final Error: {str(e)}")
                        raise e
                    logger.warning(
                        f"Function '{func.__name__}' raised error: {str(e)}. "
                        f"Retrying in {delay:.1f}s (Attempt {retries}/{max_retries_val})..."
                    )
                    time.sleep(delay)
                    delay *= backoff_factor_val
            return None
        return wrapper
    return decorator
