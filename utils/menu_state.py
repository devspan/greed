from enum import Enum
from typing import Optional
import logging

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

    def set_state(self, state: MenuState):
        self.previous_state = self.current_state
        self.current_state = state
        logger.debug(f"Menu state changed: {self.previous_state} -> {self.current_state}")

    def get_state(self) -> Optional[MenuState]:
        return self.current_state

    def go_back(self) -> Optional[MenuState]:
        if self.previous_state:
            self.current_state, self.previous_state = self.previous_state, self.current_state
            logger.debug(f"Menu state reverted: {self.previous_state} -> {self.current_state}")
        return self.current_state 