import os
from typing import Dict
import logging

logger = logging.getLogger('greed_bot')

REQUIRED_ENV_VARS = {
    'TELEGRAM_BOT_TOKEN': 'Your Telegram bot token from @BotFather',
    'DB_ENGINE': 'Database connection string (default: sqlite:///database.sqlite)'
}

def validate_env_file() -> Dict[str, str]:
    """Validate and load environment variables"""
    env_vars = {}
    
    # Check if .env exists
    if not os.path.exists('.env'):
        logger.critical("No .env file found!")
        create_env_template()
        return {}

    # Read and validate .env file
    with open('.env', 'r') as f:
        for line in f:
            if '=' in line and not line.startswith('#'):
                key, value = line.strip().split('=', 1)
                # Strip quotes and whitespace
                value = value.strip().strip('"\'')
                env_vars[key] = value

    # Validate required variables
    missing_vars = []
    invalid_vars = []
    
    for var, description in REQUIRED_ENV_VARS.items():
        if var not in env_vars:
            missing_vars.append(f"{var} ({description})")
        elif not env_vars[var] or env_vars[var] == "your-bot-token-here":
            invalid_vars.append(var)

    if missing_vars:
        logger.critical(f"Missing required environment variables: {', '.join(missing_vars)}")
        create_env_template()
        return {}

    if invalid_vars:
        logger.critical(f"Invalid values for environment variables: {', '.join(invalid_vars)}")
        return {}

    return env_vars

def create_env_template():
    """Create a template .env file"""
    try:
        with open('.env', 'w') as f:
            f.write("# Greed Bot Environment Variables\n\n")
            for var, description in REQUIRED_ENV_VARS.items():
                f.write(f"# {description}\n")
                f.write(f"{var}=\n\n")
        logger.info("Created template .env file. Please edit it with your settings.")
    except Exception as e:
        logger.error(f"Error creating .env template: {str(e)}") 