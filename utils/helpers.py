import logging
import queue
import time
from typing import List

# Thread-safe log queue for UI stream display
log_queue = queue.Queue()

class StreamlitLogHandler(logging.Handler):
    """Custom logging handler that directs log messages into a thread-safe queue for Streamlit UI integration."""
    def emit(self, record):
        log_entry = self.format(record)
        log_queue.put(log_entry)

def get_new_logs() -> List[str]:
    """Retrieve all pending log messages from the queue."""
    logs = []
    while not log_queue.empty():
        try:
            logs.append(log_queue.get_nowait())
        except queue.Empty:
            break
    return logs

def setup_logger(name: str = "recruiter_app") -> logging.Logger:
    """Setup logger with standard output and custom Streamlit UI queue handler."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    
    # Avoid duplicate handlers if setup is run multiple times
    if not logger.handlers:
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', '%H:%M:%S')
        
        # Console Handler
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        
        # Streamlit UI Handler
        ui_handler = StreamlitLogHandler()
        ui_handler.setFormatter(formatter)
        logger.addHandler(ui_handler)
        
    return logger

logger = setup_logger()

def format_eta(start_time: float, completed: int, total: int) -> str:
    """Calculate the estimated remaining time (ETA) based on average processing rate."""
    if completed == 0:
        return "Calculating..."
    elapsed = time.time() - start_time
    rate = elapsed / completed
    remaining = total - completed
    eta_seconds = int(rate * remaining)
    
    if eta_seconds < 60:
        return f"{eta_seconds}s"
    minutes = eta_seconds // 60
    seconds = eta_seconds % 60
    return f"{minutes}m {seconds}s"
