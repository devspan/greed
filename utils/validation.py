import logging
import re
import telegram
from typing import Optional, Tuple

log = logging.getLogger(__name__)

def validate_user_input(update: telegram.Update) -> Tuple[bool, Optional[str]]:
    """Validate user input from a Telegram update.
    
    Args:
        update: The Telegram update to validate
        
    Returns:
        A tuple of (is_valid, error_message)
        where is_valid is a boolean indicating if the input is valid
        and error_message is an optional string with an error message if not valid
    """
    if not update.message:
        return True, None
        
    if not update.message.text:
        return True, None
        
    # Validate text length
    if len(update.message.text) > 4096:
        return False, "Message is too long"
        
    # Validate commands
    if update.message.text.startswith('/'):
        if not re.match(r'^/[a-zA-Z0-9_]+$', update.message.text.split()[0]):
            return False, "Invalid command format"
            
    return True, None 