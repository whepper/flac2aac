#!/usr/bin/env python3
"""FLAC to AAC Converter - Entry Point

A Python application that converts FLAC files to AAC format using FDK-AAC
at the highest VBR quality, preserving metadata and adding loudness tags.
"""

import argparse
import logging
import sys
from pathlib import Path

from config import ConfigError, load_config
from pipeline import Pipeline


# Exit codes. Distinct values let shell scripts tell configuration
# problems apart from runtime encode failures.
EXIT_OK = 0
EXIT_RUNTIME = 1       # some files/albums failed, ffmpeg missing, etc.
EXIT_CONFIG = 2        # config not found, malformed, or invalid
EXIT_SIGINT = 130      # Ctrl+C


def setup_logging(level: str = "INFO") -> None:
    """Configure application logging.

    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR)
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        force=True,
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

    # Initial logging at INFO so config-load errors are visible; level
    # is refined once the configuration is parsed.
    setup_logging("INFO")
    logger = logging.getLogger(__name__)

    try:
        config = load_config(args.config)
    except FileNotFoundError:
        logger.error(f"Configuration file '{args.config}' not found.")
        return EXIT_CONFIG
    except ConfigError as e:
        logger.error(f"Invalid configuration: {e}")
        return EXIT_CONFIG
    except Exception as e:
        logger.error(f"Error loading configuration: {e}")
        return EXIT_CONFIG

    setup_logging(config.processing.log_level)

    logger.info("FLAC to AAC Converter starting")
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
        logger.info(f"Albums failed:    {stats.albums_failed}")
        logger.info("="*60)

        ran_clean = stats.failed == 0 and stats.albums_failed == 0
        return EXIT_OK if ran_clean else EXIT_RUNTIME
        
    except KeyboardInterrupt:
        logger.warning("\nOperation cancelled by user")
        return EXIT_SIGINT
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        return EXIT_RUNTIME


if __name__ == '__main__':
    sys.exit(main())
