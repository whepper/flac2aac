#!/usr/bin/env python3
"""FLAC to AAC Converter - Entry Point

A Python application that converts FLAC files to AAC format using FDK-AAC
at the highest VBR quality, preserving metadata and adding loudness tags.
"""

import argparse
import logging
import sys
from pathlib import Path

from config import load_config
from pipeline import Pipeline


def setup_logging(level: str) -> None:
    """Configure application logging.
    
    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR)
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    
    logging.basicConfig(
        level=numeric_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )


def main() -> int:
    """Main entry point.
    
    Returns:
        Exit code (0 for success, non-zero for error)
    """
    parser = argparse.ArgumentParser(
        description='Convert FLAC files to AAC with metadata and loudness tagging'
    )
    parser.add_argument(
        '--config',
        type=Path,
        default=Path('config.toml'),
        help='Path to configuration file (default: config.toml)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be processed without encoding'
    )
    
    args = parser.parse_args()
    
    # Load configuration
    try:
        config = load_config(args.config)
    except FileNotFoundError:
        print(f"Error: Configuration file '{args.config}' not found.", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error loading configuration: {e}", file=sys.stderr)
        return 1
    
    # Setup logging
    setup_logging(config.processing.log_level)
    logger = logging.getLogger(__name__)
    
    logger.info(f"FLAC to AAC Converter starting")
    logger.info(f"Input: {config.paths.input_dir}")
    logger.info(f"Output: {config.paths.output_dir}")
    logger.info(f"Workers: {config.processing.workers}")
    
    # Run pipeline
    try:
        pipeline = Pipeline(config, dry_run=args.dry_run)
        stats = pipeline.run()
        
        # Print summary
        logger.info("\n" + "="*60)
        logger.info("Conversion Summary")
        logger.info("="*60)
        logger.info(f"Total files processed: {stats.total_files}")
        logger.info(f"Successful: {stats.successful}")
        logger.info(f"Failed: {stats.failed}")
        logger.info(f"Skipped: {stats.skipped}")
        logger.info(f"Albums processed: {stats.albums_processed}")
        logger.info("="*60)
        
        return 0 if stats.failed == 0 else 1
        
    except KeyboardInterrupt:
        logger.warning("\nOperation cancelled by user")
        return 130
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        return 1


if __name__ == '__main__':
    sys.exit(main())
