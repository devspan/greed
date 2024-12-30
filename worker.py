import logging
import sys
import datetime
import queue as queuem
import re
import threading
import traceback
import uuid
from html import escape
from typing import *
import io
import csv
import requests
import sqlalchemy
from sqlalchemy.orm import Session
import telegram
import database as db
from database import DatabaseManager
import localization
import nuconfig
from menus.menu_manager import MenuManager
from menus.menu_state import MenuState
from menus.main_menu import MainMenu
from utils.logger import logger
from sqlalchemy.engine import Engine
from contextlib import contextmanager

log = logging.getLogger(__name__)


def format_price(price: int, currency_symbol: str = None) -> str:
    """Format a price with the currency symbol."""
    if currency_symbol is None:
        currency_symbol = "€"
    return f"{currency_symbol}{int(price)/100:.2f}"


class Price(int):
    """A class that represents a price value in the bot's currency.
    It can be formatted as a string to show the value to the user, and supports basic arithmetic."""

    def __new__(cls, number, worker=None):
        instance = super(Price, cls).__new__(cls, int(number))
        instance.worker = worker
        return instance

    def __str__(self):
        if hasattr(self, 'worker') and self.worker:
            currency_symbol = self.worker.cfg["Payments"]["currency_symbol"]
        else:
            currency_symbol = None
        return format_price(int(self), currency_symbol=currency_symbol)

    def __format__(self, format_spec):
        return format(str(self), format_spec)

    def __add__(self, other):
        return self.__class__(int(self) + int(other), worker=self.worker)

    def __sub__(self, other):
        return self.__class__(int(self) - int(other), worker=self.worker)

    def __mul__(self, other):
        return self.__class__(int(self) * int(other), worker=self.worker)

    def __floordiv__(self, other):
        return self.__class__(int(self) // int(other), worker=self.worker)

    def __truediv__(self, other):
        return self.__class__(int(int(self) / int(other)), worker=self.worker)


class StopSignal:
    """A data class that should be sent to the worker when the conversation has to be stopped abnormally."""

    def __init__(self, reason: str = ""):
        self.reason = reason


class CancelSignal:
    """An empty class that is added to the queue whenever the user presses a cancel inline button."""
    pass


class Worker:
    """A worker for a single conversation. A new worker is created every time the /start command is sent."""

    def __init__(self, bot, chat: telegram.Chat, telegram_user: telegram.User, cfg: dict, engine):
        """Initialize the worker"""
        self.name = f"Worker {chat.id}"
        self.bot = bot
        self.chat = chat
        self.telegram_user = telegram_user
        self.cfg = cfg
        self.engine = engine
        self.db = DatabaseManager(engine)
        self.user = None
        self.admin = None
        self.loc = None
        self.menu_manager = None
        self.session = None  # Add session attribute
        
        # Initialize in proper order
        self.__init_database()
        self.__create_localization()
        self.__init_menu_manager()

    def __init_database(self):
        """Initialize database connection and get user data"""
        try:
            # Create a session that will stay open
            self.session = self.db.create_session()
            
            # Get existing user or create new one
            self.user = self.session.query(db.User).filter(
                db.User.user_id == self.telegram_user.id
            ).one_or_none()
            
            if not self.user:
                self.user = db.User(
                    user_id=self.telegram_user.id,
                    first_name=self.telegram_user.first_name,
                    last_name=self.telegram_user.last_name,
                    username=self.telegram_user.username,
                    language=self.telegram_user.language_code or self.cfg["Language"]["default_language"]
                )
                self.session.add(self.user)
                self.session.commit()
            
            # Check if user is admin
            self.admin = self.session.query(db.Admin).filter(
                db.Admin.user_id == self.telegram_user.id
            ).one_or_none()
            
        except Exception as e:
            logger.error(f"Database initialization error: {str(e)}")
            if self.session:
                self.session.rollback()
            raise

    def __create_localization(self):
        """Create a localization object for the worker"""
        try:
            # Try getting the user's language from the database
            language = None
            if self.user and self.user.language:
                language = self.user.language
            # Otherwise try getting it from Telegram
            elif self.telegram_user.language_code:
                language = self.telegram_user.language_code
            # If none of these are available, use English
            if not language or language not in self.cfg["Language"]["enabled_languages"]:
                language = self.cfg["Language"]["default_language"]
            
            # Create a new Localization object with proper parameters
            self.loc = localization.Localization(
                language=language,
                fallback=self.cfg["Language"]["default_language"]
            )
            logger.debug(f"Created localization for language: {language}")
            
        except Exception as e:
            logger.error(f"Error creating localization: {str(e)}")
            raise

    def __init_menu_manager(self):
        """Initialize the menu manager"""
        try:
            self.menu_manager = MenuManager(self)
            self.menu_manager.register_menu('main', MainMenu)
            logger.debug("Menu manager initialized successfully")
        except Exception as e:
            logger.error(f"Menu manager initialization error: {str(e)}")
            raise

    def is_ready(self) -> bool:
        """Check if the worker is properly initialized"""
        return all([
            self.cfg is not None,
            self.db is not None,
            self.loc is not None,
            self.menu_manager is not None,
            self.user is not None  # Make sure user exists in database
        ])

    async def start(self):
        """Start the worker and show main menu"""
        try:
            if not self.is_ready():
                raise RuntimeError("Worker not properly initialized")
            
            # Refresh the user object to ensure it's attached to session
            self.session.refresh(self.user)
            
            # Show main menu
            await self.menu_manager.show_menu('main')
            logger.debug(f"Started worker for chat {self.chat.id}")
            
        except Exception as e:
            logger.error(f"Error starting worker: {str(e)}")
            raise

    async def send_message(self, text: str, **kwargs):
        """Send a message to the chat"""
        try:
            return await self.bot.send_message(
                chat_id=self.chat.id,
                text=text,
                **kwargs
            )
        except Exception as e:
            logger.error(f"Error sending message: {str(e)}")
            raise

    def cleanup(self):
        """Cleanup resources"""
        if self.session:
            self.session.close()

    def __del__(self):
        """Destructor to ensure cleanup"""
        self.cleanup()
