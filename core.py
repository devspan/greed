import logging
import sys
import os
import signal
import traceback
import nuconfig
import database as db
import telegram
from telegram import error as telegram_error
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, PreCheckoutQueryHandler, filters
import worker
from typing import Dict
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
import localization
from dotenv import load_dotenv
from utils.logger import log_error, log_command, log_callback, logger
from middleware.menu_handler import ensure_menu_state
from menus.menu_state import MenuState
import asyncio
from contextlib import suppress
from utils.env_loader import validate_env_file

# Enable detailed logging first
logging.basicConfig(
    format="{asctime} | {threadName} | {name} | {levelname} | {message}",
    style='{',
    level=logging.DEBUG,  # Set to DEBUG for more detailed logs
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log')  # Also log to file
    ]
)

# Create logger
log = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
log.debug(f"Bot token from env: {os.getenv('TELEGRAM_BOT_TOKEN')}")

def signal_handler(sig, frame):
    print('\nShutting down gracefully...')
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

# Global variables
chat_workers: Dict[int, worker.Worker] = {}
user_cfg = None
engine = None

@log_command
@log_error
async def start_command(update: telegram.Update, context):
    """Handler for /start command"""
    try:
        # Cleanup old worker if exists
        if 'worker' in context.chat_data:
            old_worker = context.chat_data['worker']
            old_worker.cleanup()
            
        # Create and initialize worker
        new_worker = worker.Worker(
            bot=context.bot,
            chat=update.message.chat,
            telegram_user=update.message.from_user,
            cfg=context.bot_data['config'],
            engine=context.bot_data['engine']
        )
        
        # Store worker in chat_data
        context.chat_data['worker'] = new_worker
        
        # Check if worker is ready
        if not new_worker.is_ready():
            await update.message.reply_text(
                context.bot_data["default_loc"].get("error_worker_not_ready")
            )
            return
            
        # Start the worker and show main menu
        await new_worker.start()

    except Exception as e:
        logger.error(f"Error in start_command: {str(e)}")
        await update.message.reply_text(
            "An error occurred while starting the bot. Please try again later."
        )
        raise

@ensure_menu_state
@log_command
@log_error
async def message_handler(update: telegram.Update, context):
    """Handler for text messages"""
    try:
        chat_id = update.message.chat_id
        log.debug(f"Received message from {chat_id}: {update.message.text[:20]}...")
        receiving_worker = chat_workers.get(chat_id)

        # Ensure a worker exists for the chat and is alive
        if receiving_worker is None:
            await update.message.reply_text(
                context.bot_data["default_loc"].get("error_no_worker_for_chat"),
                reply_markup=telegram.ReplyKeyboardRemove()
            )
            return

        # If the worker is not ready...
        if not receiving_worker.is_ready():
            await update.message.reply_text(
                context.bot_data["default_loc"].get("error_worker_not_ready"),
                reply_markup=telegram.ReplyKeyboardRemove()
            )
            return

        # Get the message text
        message_text = update.message.text.strip()

        # If the message contains the "Cancel" string defined in the strings file...
        if message_text == receiving_worker.loc.get("menu_cancel"):
            log.debug(f"Forwarding CancelSignal to {receiving_worker}")
            receiving_worker.queue.put(worker.CancelSignal())
        else:
            log.debug(f"Forwarding message to {receiving_worker}")
            receiving_worker.queue.put(update)
    except Exception as e:
        log.error(f"Error in message_handler: {str(e)}")
        log.error(traceback.format_exc())
        try:
            await update.message.reply_text(
                "An error occurred while processing your request. Please try again later or contact support if the problem persists.",
                reply_markup=telegram.ReplyKeyboardRemove()
            )
        except:
            pass
        raise

@log_callback
@log_error
async def callback_query_handler(update: telegram.Update, context):
    """Handle callback queries from inline keyboards"""
    query = update.callback_query
    try:
        # Get or create worker
        chat_id = update.effective_chat.id
        worker = context.chat_data.get('worker')
        
        if not worker:
            await query.answer("Session expired. Please use /start to begin again.")
            return

        # Map callback data to menu states and handlers
        callback_map = {
            "order": (MenuState.ORDER, worker.show_order_menu),
            "order_status": (MenuState.ORDER_STATUS, worker.show_order_status),
            "add_credit": (MenuState.ADD_CREDIT, worker.show_add_credit),
            "language": (MenuState.LANGUAGE, worker.show_language_selection),
            "help": (MenuState.HELP, worker.show_help),
            "bot_info": (MenuState.BOT_INFO, worker.show_bot_info),
            "back": (MenuState.MAIN, worker.show_menu)
        }

        if query.data in callback_map:
            state, handler = callback_map[query.data]
            worker.menu_manager.set_state(state)
            await handler()
            await query.answer()
        else:
            await query.answer("Invalid option")
            logger.warning(f"Unhandled callback data: {query.data}")

    except Exception as e:
        logger.error(f"Error handling callback query: {str(e)}")
        await query.answer("An error occurred. Please try again.")

async def pre_checkout_handler(update: telegram.Update, context):
    """Handler for pre-checkout queries"""
    try:
        if not update.pre_checkout_query:
            return

        log.debug(f"Received pre-checkout query: {update.pre_checkout_query.id}")
        receiving_worker = chat_workers.get(update.pre_checkout_query.from_user.id)
        
        if (receiving_worker is None or 
                update.pre_checkout_query.invoice_payload != receiving_worker.invoice_payload):
            try:
                await update.pre_checkout_query.answer(
                    ok=False,
                    error_message=context.bot_data["default_loc"].get("error_invoice_expired")
                )
            except telegram.error.BadRequest:
                log.error("pre-checkout query expired before an answer could be sent!")
            return

        log.debug(f"Forwarding pre-checkout query to {receiving_worker}")
        receiving_worker.queue.put(update)
    except Exception as e:
        log.error(f"Error in pre_checkout_handler: {str(e)}")
        log.error(traceback.format_exc())
        try:
            await update.pre_checkout_query.answer(ok=False, error_message="An error occurred. Please try again later.")
        except:
            pass
        raise

async def error_handler(update: telegram.Update, context):
    """Log errors caused by Updates."""
    log.error(f'Update "{update}" caused error "{context.error}"')
    log.error(traceback.format_exc())
    
    # Try to notify user of error
    try:
        if update and update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="An error occurred while processing your request. Please try again later or contact support if the problem persists."
            )
    except:
        pass

@log_callback
@log_error
async def handle_language_selection(update: telegram.Update, context):
    """Handle language selection callback"""
    query = update.callback_query
    if not query.data.startswith("lang_"):
        return False
        
    try:
        lang_code = query.data.split("_")[1]
        if lang_code not in context.bot_data["supported_languages"]:
            await query.answer("Invalid language selection")
            return True

        chat_id = query.from_user.id
        
        # Update user language in database
        with Session(engine) as session:
            user = session.query(db.User).filter(db.User.user_id == chat_id).first()
            if not user:
                user = db.User(user_id=chat_id, language=lang_code)
                session.add(user)
            else:
                user.language = lang_code
            session.commit()

        # Confirm language change
        lang_name = context.bot_data["supported_languages"][lang_code]
        await query.message.edit_text(f"Language set to {lang_name}")
        
        # Start new worker with selected language
        await start_command(update, context)
        
        return True
    except Exception as e:
        log.error(f"Error handling language selection: {str(e)}")
        await query.answer("Error setting language. Please try again.")
        return True

@log_command
@log_error
async def language_command(update: telegram.Update, context):
    """Handler for /language command"""
    try:
        chat_id = update.message.chat_id
        keyboard = []
        for lang_code in user_cfg["Language"]["enabled_languages"]:
            keyboard.append([telegram.InlineKeyboardButton(
                context.bot_data["supported_languages"].get(lang_code, lang_code),
                callback_data=f"lang_{lang_code}"
            )])
        
        reply_markup = telegram.InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "Please select your language / Seleziona la tua lingua:",
            reply_markup=reply_markup
        )
    except Exception as e:
        log.error(f"Error in language_command: {str(e)}")
        log.error(traceback.format_exc())
        await update.message.reply_text("Error changing language. Please try again.")

class BotApplication:
    def __init__(self):
        self.app = None
        self.bot_token = None
        self.config = None
        self.engine = None
        self._shutdown_event = asyncio.Event()
        
    async def initialize(self):
        """Initialize bot configuration and database"""
        try:
            # Load environment variables
            env_vars = validate_env_file()
            if not env_vars:
                sys.exit(1)
                
            # Get bot token and ensure it's clean
            self.bot_token = env_vars['TELEGRAM_BOT_TOKEN'].strip().strip('"\'')
            if not self.bot_token or self.bot_token == "your-bot-token-here":
                logger.critical("Invalid bot token! Please set a valid token in .env file")
                sys.exit(1)
                
            logger.debug("Successfully loaded configuration from .env file")
            
            # Load config file
            logger.debug("Loading config file...")
            logger.debug("Reading config from config/config.toml")
            self.config = nuconfig.NuConfig("config/config.toml")
            
            # Initialize database with best practices
            logger.debug("Creating the sqlalchemy engine...")
            db_url = env_vars.get('DB_ENGINE', 'sqlite:///database.sqlite')
            # Remove any extra quotes
            db_url = db_url.strip('"\'')
            
            # Create engine with recommended settings
            self.engine = create_engine(
                db_url,
                # Enable connection pooling
                pool_pre_ping=True,  # Enable connection health checks
                pool_recycle=3600,   # Recycle connections after 1 hour
                # SQLite specific optimizations
                connect_args={'check_same_thread': False} if db_url.startswith('sqlite') else {},
                echo=False,  # Set to True for SQL query logging
                future=True  # Use SQLAlchemy 2.0 style
            )
            
            # Create tables
            logger.debug("Creating all missing tables...")
            db.Base.metadata.create_all(self.engine)
            
            # Create the bot
            self.app = Application.builder().token(self.bot_token).build()
            
            # Add handlers
            self.app.add_handler(CommandHandler("start", start_command))
            self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
            self.app.add_handler(CallbackQueryHandler(callback_query_handler))
            
            # Store common data
            self.app.bot_data["config"] = self.config
            self.app.bot_data["engine"] = self.engine
            
        except Exception as e:
            logger.error(f"Error during initialization: {str(e)}")
            raise

    async def run(self):
        """Run the bot application"""
        try:
            await self.initialize()
            logger.info("Starting bot...")
            
            # Initialize and start the application
            await self.app.initialize()
            await self.app.start()
            
            # Start polling in the background
            polling_task = asyncio.create_task(
                self.app.updater.start_polling(
                    allowed_updates=telegram.Update.ALL_TYPES,
                    drop_pending_updates=True
                )
            )
            
            # Wait for shutdown signal
            await self._shutdown_event.wait()
            
            # Cancel polling and cleanup
            polling_task.cancel()
            with suppress(asyncio.CancelledError):
                await polling_task
                
        except Exception as e:
            logger.error(f"Error starting bot: {str(e)}")
            raise
        finally:
            await self.shutdown()

    async def shutdown(self):
        """Shutdown the bot application"""
        try:
            logger.info("Stopping bot application...")
            if self.app and self.app.running:
                await self.app.stop()
                await self.app.shutdown()
            if self.engine:
                self.engine.dispose()
        except Exception as e:
            logger.error(f"Error during shutdown: {str(e)}")

def main():
    """Main entry point"""
    bot = BotApplication()
    
    def signal_handler():
        """Handle shutdown signals"""
        if not bot._shutdown_event.is_set():
            logger.info("Received shutdown signal")
            asyncio.get_event_loop().call_soon_threadsafe(
                bot._shutdown_event.set
            )
    
    try:
        # Setup signal handlers
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, signal_handler)
            
        # Run bot until complete
        loop.run_until_complete(bot.run())
        
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
    finally:
        try:
            # Clean shutdown
            tasks = [t for t in asyncio.all_tasks(loop) 
                    if t is not asyncio.current_task()]
            
            for task in tasks:
                task.cancel()
                
            loop.run_until_complete(
                asyncio.gather(*tasks, return_exceptions=True)
            )
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
        except Exception as e:
            logger.error(f"Error during cleanup: {str(e)}")

if __name__ == "__main__":
    main()
