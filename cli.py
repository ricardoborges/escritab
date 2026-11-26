"""Batch CLI: process every song in a folder (see app/batch.py or `python cli.py --help`)."""
import sys

from app.batch import main

if __name__ == "__main__":
    sys.exit(main())
