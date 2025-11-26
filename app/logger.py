import logging
import os
from datetime import datetime

def setup_logging(log_level=None):
    """
    Set up logging with a timestamped log file and configurable log level.
    Log file is created in the 'logs' directory with a timestamp in the filename.
    """
    if not os.path.exists('logs'):
        os.makedirs('logs')
    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    log_filename = f'logs/server_{timestamp}.log'

    # Get log level from argument or environment variable, default to INFO
    if log_level is None:
        log_level = os.environ.get('LOG_LEVEL', 'INFO').upper()
    numeric_level = getattr(logging, log_level, logging.INFO)

    logging.basicConfig(
        level=numeric_level,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        handlers=[
            logging.FileHandler(log_filename),
            logging.StreamHandler()
        ]
    )
    logging.info(f"Logging started. Level: {log_level}, File: {log_filename}")
    return logging.getLogger()
