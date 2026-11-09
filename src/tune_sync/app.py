"""Command-line entry point; startup only validates local configuration."""

import argparse
from pathlib import Path
import sys

from .conf import ConfigurationError, load_conf


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate Tune Sync configuration locally.")
    parser.add_argument("--env-file", type=Path, help="Path to an existing .env file")
    args = parser.parse_args(argv)
    if args.env_file is not None and not args.env_file.is_file():
        print("Configuration error: --env-file must point to an existing file.", file=sys.stderr)
        return 1
    try:
        load_conf(args.env_file)
    except (ConfigurationError, OSError) as error:
        message = str(error) if isinstance(error, ConfigurationError) else "Cannot read configuration file."
        print(f"Configuration error:\n{message}", file=sys.stderr)
        return 1
    print("Configuration is valid. No login or Spotify API requests were made.")
    return 0
