from .base import BaseMenu
import telegram
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger('greed_bot')

class MainMenu(BaseMenu):
    def __init__(self, worker):
        super().__init__(worker)
        self.current_message_id: Optional[int] = None

    async def display(self):
        """Display the main menu"""
        try:
            keyboard = [
                [
                    telegram.InlineKeyboardButton(
                        self.loc.get("menu_order"), 
                        callback_data="main:order"
                    ),
                    telegram.InlineKeyboardButton(
                        self.loc.get("menu_order_status"), 
                        callback_data="main:order_status"
                    )
                ],
                [
                    telegram.InlineKeyboardButton(
                        self.loc.get("menu_add_credit"), 
                        callback_data="main:add_credit"
                    ),
                    telegram.InlineKeyboardButton(
                        self.loc.get("menu_language"), 
                        callback_data="main:language"
                    )
                ],
                [
                    telegram.InlineKeyboardButton(
                        self.loc.get("menu_help"), 
                        callback_data="main:help"
                    ),
                    telegram.InlineKeyboardButton(
                        self.loc.get("menu_bot_info"), 
                        callback_data="main:bot_info"
                    )
                ]
            ]
            markup = telegram.InlineKeyboardMarkup(keyboard)
            
            message = await self.send_or_edit(
                self.loc.get("conversation_open_user_menu", 
                            credit=str(self.worker.user.credit)),
                markup,
                self.current_message_id
            )
            self.current_message_id = message.message_id
            
        except Exception as e:
            logger.error(f"Error displaying main menu: {str(e)}")
            raise

    async def handle_callback(self, callback_query: telegram.CallbackQuery) -> bool:
        """Handle callback queries for main menu"""
        try:
            action = callback_query.data.split(':')[1]
            
            # Map actions to menu states and handlers
            handlers = {
                'order': self.worker.show_order_menu,
                'order_status': self.worker.show_order_status,
                'add_credit': self.worker.show_add_credit,
                'language': self.worker.show_language_selection,
                'help': self.worker.show_help,
                'bot_info': self.worker.show_bot_info
            }

            if action in handlers:
                await handlers[action]()
                await callback_query.answer()
                return True

            return False

        except Exception as e:
            logger.error(f"Error handling main menu callback: {str(e)}")
            await callback_query.answer("An error occurred")
            return False 