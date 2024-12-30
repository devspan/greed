import telegram
from typing import Dict, Any
import logging

logger = logging.getLogger('greed_bot')

class CallbackHandler:
    def __init__(self, worker):
        self.worker = worker
        self.menus: Dict[str, Any] = {}  # Will store menu instances

    async def handle_callback_query(self, update: telegram.Update, context):
        """Handle all callback queries"""
        query = update.callback_query
        try:
            # Get menu prefix from callback data
            menu_prefix = query.data.split(':')[0]
            
            # Get appropriate menu handler
            menu = self.menus.get(menu_prefix)
            if menu:
                handled = await menu.handle_callback(query)
                if handled:
                    return

            logger.warning(f"Unhandled callback query: {query.data}")
            await query.answer("Invalid option")

        except Exception as e:
            logger.error(f"Error in callback handler: {str(e)}")
            await query.answer("An error occurred") 