from functools import wraps
from utils.logger import logger
import telegram
from typing import Callable, Any
from utils.menu_state import MenuState

def ensure_menu_state(func: Callable) -> Callable:
    """Decorator to ensure proper menu state"""
    @wraps(func)
    async def wrapper(update: telegram.Update, context, *args, **kwargs) -> Any:
        try:
            # Check if we have a valid chat session
            if not context or not update.effective_chat:
                logger.error(f"Invalid menu state: Missing context or chat")
                return

            # Get or create worker
            chat_id = update.effective_chat.id
            worker = context.chat_data.get('worker')
            
            if not worker:
                logger.warning(f"No worker found for chat {chat_id}, creating new one")
                # Initialize new worker
                worker = context.dispatcher.chat_data[chat_id]['worker'] = Worker(
                    bot=context.bot,
                    chat=update.effective_chat,
                    telegram_user=update.effective_user,
                    cfg=context.bot_data['config'],
                    engine=context.bot_data['engine']
                )
                worker.menu_manager.set_state(MenuState.MAIN)

            # Log menu interaction
            logger.debug(
                f"Menu interaction: {update.message.text if update.message else 'callback'} | "
                f"User: {update.effective_user.id} | "
                f"State: {worker.menu_manager.get_state()}"
            )

            return await func(update, context, *args, **kwargs)
            
        except Exception as e:
            logger.error(f"Menu handler error: {str(e)}", exc_info=True)
            try:
                await update.effective_chat.send_message(
                    "There was an error processing your request. Please try /start to reset the menu."
                )
            except:
                pass
            return None
            
    return wrapper 