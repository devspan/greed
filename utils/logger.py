import logging
import sys
import traceback
from functools import wraps
from typing import Callable, Any
import telegram
from datetime import datetime
import os

# Create logs directory if it doesn't exist
os.makedirs('logs', exist_ok=True)

# Create file handlers
main_handler = logging.FileHandler(f'logs/bot_{datetime.now().strftime("%Y%m%d")}.log')
error_handler = logging.FileHandler(f'logs/errors_{datetime.now().strftime("%Y%m%d")}.log')
error_handler.setLevel(logging.ERROR)  # Set level after creation

# Configure the main logger
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s',
    handlers=[
        # Console handler
        logging.StreamHandler(sys.stdout),
        # File handler for all logs
        main_handler,
        # Separate file for errors
        error_handler
    ]
)

logger = logging.getLogger('greed_bot')

def log_error(func: Callable) -> Callable:
    """Decorator to log any errors that occur in a function"""
    @wraps(func)
    async def wrapper(*args, **kwargs) -> Any:
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            # Get the current update and context if available
            update = next((arg for arg in args if isinstance(arg, telegram.Update)), None)
            
            # Log the error with full context
            error_msg = f"""
ERROR in {func.__name__}:
Function: {func.__name__}
Args: {args}
Kwargs: {kwargs}
Error Type: {type(e).__name__}
Error Message: {str(e)}
Traceback:
{traceback.format_exc()}
            """
            logger.error(error_msg)

            # If this is a user-facing function, notify the user
            if update and hasattr(update, 'effective_chat'):
                try:
                    # Get the localization from context if available
                    context = args[1] if len(args) > 1 else None
                    loc = context.bot_data["default_loc"] if context and "default_loc" in context.bot_data else None
                    
                    error_message = loc.get("error_generic") if loc else "An error occurred. Please try again later."
                    
                    await update.effective_chat.send_message(
                        text=error_message,
                        parse_mode=telegram.constants.ParseMode.HTML
                    )
                except Exception as notify_error:
                    logger.error(f"Failed to notify user of error: {str(notify_error)}")
            
            # Re-raise the exception if it's critical
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                raise
            
            return None
    return wrapper

def log_command(func: Callable) -> Callable:
    """Decorator to log command usage"""
    @wraps(func)
    async def wrapper(*args, **kwargs) -> Any:
        update = next((arg for arg in args if isinstance(arg, telegram.Update)), None)
        if update and update.effective_user:
            logger.info(
                f"Command: {func.__name__} | "
                f"User: {update.effective_user.id} | "
                f"Username: {update.effective_user.username} | "
                f"Chat: {update.effective_chat.id if update.effective_chat else 'N/A'}"
            )
        return await func(*args, **kwargs)
    return wrapper

def log_callback(func: Callable) -> Callable:
    """Decorator to log callback query handling"""
    @wraps(func)
    async def wrapper(*args, **kwargs) -> Any:
        update = next((arg for arg in args if isinstance(arg, telegram.Update)), None)
        if update and update.callback_query:
            logger.info(
                f"Callback: {update.callback_query.data} | "
                f"User: {update.effective_user.id} | "
                f"Username: {update.effective_user.username}"
            )
        return await func(*args, **kwargs)
    return wrapper

def catch_errors(func: Callable) -> Callable:
    """Decorator to catch and handle all errors"""
    @wraps(func)
    async def wrapper(*args, **kwargs) -> Any:
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in {func.__name__}: {str(e)}\n{traceback.format_exc()}")
            update = next((arg for arg in args if isinstance(arg, telegram.Update)), None)
            if update and hasattr(update, 'effective_chat'):
                try:
                    await update.effective_chat.send_message(
                        "An error occurred. Our team has been notified."
                    )
                except:
                    pass
            return None
    return wrapper 