#!/usr/bin/env python3
"""One-shot script to bulk-load all NSE CSV files into TimescaleDB."""
import sys
import os
import logging
import time

sys.path.insert(0, os.path.dirname(__file__))
os.chdir(os.path.dirname(__file__))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/media/vboxuser/test/NSE_Data/bulk_load.log'),
    ]
)
logger = logging.getLogger(__name__)

from app.ingest.bulk_db_loader import load_files_to_db

if __name__ == '__main__':
    start = time.time()
    sources = sys.argv[1:] if len(sys.argv) > 1 else ['all']
    for src in sources:
        logger.info(f"=== Starting bulk load: {src} ===")
        try:
            result = load_files_to_db(src, file_format="csv", batch_size=100)
            logger.info(f"=== {src} result: {result} ===")
        except Exception as e:
            logger.error(f"=== {src} FAILED: {e} ===", exc_info=True)
    elapsed = time.time() - start
    logger.info(f"=== ALL DONE in {elapsed/60:.1f} minutes ===")
