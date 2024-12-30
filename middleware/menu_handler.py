from functools import wraps
from utils.logger import logger
import telegram
from typing import Callable, Any
from menus.menu_state import MenuState
from worker import Worker

def ensure_menu_state(func: Callable) -> Callable:
    """Decorator to ensure proper menu state"""
    @wraps(func)
    async def wrapper(update: telegram.Update, context, *args, **kwargs) -> Any:
        try:
            if not context or not update.effective_chat:
                logger.error(f"Invalid menu state: Missing context or chat")
                return

            chat_id = update.effective_chat.id
            worker = context.chat_data.get('worker')
            
            # Check session validity
            if worker and not worker.menu_manager.is_session_valid():
                logger.warning(f"Session timeout for chat {chat_id}")
                await update.effective_chat.send_message(
                    "Your session has expired. Please use /start to begin again.",
                    reply_markup=telegram.ReplyKeyboardRemove()
                )
                context.chat_data.pop('worker', None)
                return

            if not worker:
                logger.warning(f"No worker found for chat {chat_id}, creating new one")
                worker = context.chat_data['worker'] = Worker(
                    bot=context.bot,
                    chat=update.effective_chat,
                    telegram_user=update.effective_user,
                    cfg=context.bot_data['config'],
                    engine=context.bot_data['engine']
                )
                worker.menu_manager.set_state(MenuState.MAIN)

            # Update last activity
            worker.menu_manager.update_activity()

            return await func(update, context, *args, **kwargs)
            
        except Exception as e:
            logger.error(f"Menu handler error: {str(e)}", exc_info=True)
            try:
                await update.effective_chat.send_message(
                    "There was an error processing your request. Please try /start to reset the menu.",
                    reply_markup=telegram.ReplyKeyboardRemove()
                )
            except:
                pass
            return None
    return wrapper 