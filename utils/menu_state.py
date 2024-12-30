from enum import Enum
from typing import Optional
import logging
from datetime import datetime, timedelta

logger = logging.getLogger('greed_bot')

class MenuState(Enum):
    MAIN = "main"
    ORDER = "order"
    ORDER_STATUS = "order_status"
    ADD_CREDIT = "add_credit"
    LANGUAGE = "language"
    HELP = "help"
    BOT_INFO = "bot_info"

class MenuManager:
    def __init__(self):
        self.current_state: Optional[MenuState] = None
        self.previous_state: Optional[MenuState] = None
        self.last_activity: datetime = datetime.now()
        self.SESSION_TIMEOUT = timedelta(minutes=30)

    def is_session_valid(self) -> bool:
        """Check if the current session is still valid"""
        return datetime.now() - self.last_activity < self.SESSION_TIMEOUT

    def update_activity(self):
        """Update the last activity timestamp"""
        self.last_activity = datetime.now()

    def set_state(self, state: MenuState):
        self.previous_state = self.current_state
        self.current_state = state
        self.update_activity()
        logger.debug(f"Menu state changed: {self.previous_state} -> {self.current_state}")

    def get_state(self) -> Optional[MenuState]:
        return self.current_state

    def go_back(self) -> Optional[MenuState]:
        if self.previous_state:
            self.current_state, self.previous_state = self.previous_state, self.current_state
            logger.debug(f"Menu state reverted: {self.previous_state} -> {self.current_state}")
        return self.current_state 