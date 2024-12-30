from typing import Dict, Type, Optional
import logging
from datetime import datetime, timedelta
from .base import BaseMenu
from .main_menu import MainMenu
from .menu_state import MenuState
import signal
import sys

logger = logging.getLogger('greed_bot')

class MenuManager:
    def __init__(self, worker):
        self.worker = worker
        self.menus: Dict[str, BaseMenu] = {}
        self.current_menu: Optional[BaseMenu] = None
        self.current_state: Optional[MenuState] = None
        self.previous_state: Optional[MenuState] = None
        self.last_activity: datetime = datetime.now()
        self.SESSION_TIMEOUT = timedelta(minutes=30)
        self._setup_signal_handlers()
        
    def _setup_signal_handlers(self):
        """Setup graceful shutdown handlers"""
        def signal_handler(signum, frame):
            logger.info("Received shutdown signal, cleaning up...")
            self.cleanup()
            sys.exit(0)
            
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

    def is_session_valid(self) -> bool:
        """Check if the current session is still valid"""
        return datetime.now() - self.last_activity < self.SESSION_TIMEOUT

    def update_activity(self):
        """Update the last activity timestamp"""
        self.last_activity = datetime.now()

    def set_state(self, state: MenuState):
        """Set the current menu state"""
        self.previous_state = self.current_state
        self.current_state = state
        self.update_activity()
        logger.debug(f"Menu state changed: {self.previous_state} -> {self.current_state}")

    def get_state(self) -> Optional[MenuState]:
        """Get current menu state"""
        return self.current_state if self.is_session_valid() else None

    def register_menu(self, prefix: str, menu_class: Type[BaseMenu]) -> BaseMenu:
        """Register a menu with a prefix"""
        menu = menu_class(self.worker)
        self.menus[prefix] = menu
        return menu

    def get_menu(self, prefix: str) -> Optional[BaseMenu]:
        """Get a menu by its prefix"""
        return self.menus.get(prefix)

    async def show_menu(self, prefix: str):
        """Display a specific menu"""
        if not self.is_session_valid():
            await self.worker.send_message(self.worker.loc.get("error_menu_timeout"))
            return

        menu = self.get_menu(prefix)
        if menu:
            self.current_menu = menu
            await menu.display()
        else:
            logger.error(f"Menu not found: {prefix}")

    async def handle_callback(self, callback_query) -> bool:
        """Route callback to appropriate menu"""
        try:
            if not self.is_session_valid():
                await callback_query.answer(self.worker.loc.get("error_menu_timeout"))
                return False

            prefix = callback_query.data.split(':')[0]
            menu = self.get_menu(prefix)
            if menu:
                return await menu.handle_callback(callback_query)
            return False
        except Exception as e:
            logger.error(f"Error handling callback: {str(e)}")
            return False

    def cleanup(self):
        """Cleanup resources before shutdown"""
        try:
            logger.info("Cleaning up menu resources...")
            for menu in self.menus.values():
                if hasattr(menu, 'cleanup'):
                    menu.cleanup()
        except Exception as e:
            logger.error(f"Error during cleanup: {str(e)}") 