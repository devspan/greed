from abc import ABC, abstractmethod
import telegram
from typing import List, Optional
import logging

logger = logging.getLogger('greed_bot')

class BaseMenu(ABC):
    def __init__(self, worker):
        self.worker = worker
        self.bot = worker.bot
        self.chat_id = worker.chat.id
        self.loc = worker.loc

    @abstractmethod
    async def display(self):
        """Display the menu"""
        pass

    @abstractmethod
    async def handle_callback(self, callback_query: telegram.CallbackQuery) -> bool:
        """Handle callback queries for this menu"""
        pass

    async def send_or_edit(self, 
                          text: str, 
                          keyboard: telegram.InlineKeyboardMarkup,
                          message_id: Optional[int] = None) -> telegram.Message:
        """Send a new message or edit existing one with the menu"""
        try:
            if message_id:
                return await self.bot.edit_message_text(
                    chat_id=self.chat_id,
                    message_id=message_id,
                    text=text,
                    reply_markup=keyboard,
                    parse_mode='HTML'
                )
            else:
                return await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=text,
                    reply_markup=keyboard,
                    parse_mode='HTML'
                )
        except Exception as e:
            logger.error(f"Error in send_or_edit: {str(e)}")
            raise 