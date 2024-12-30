import logging
import sys
import os
import traceback
import nuconfig
import database as db
import telegram
from telegram import error as telegram_error
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, PreCheckoutQueryHandler, filters
import worker
import threading
from typing import Dict
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
import localization
import signal

def signal_handler(sig, frame):
    print('\nShutting down gracefully...')
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

# Enable detailed logging
logging.basicConfig(
    format="{asctime} | {threadName} | {name} | {levelname} | {message}",
    style='{',
    level=logging.DEBUG,  # Set to DEBUG for more detailed logs
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log')  # Also log to file
    ]
)

log = logging.getLogger(__name__)

# Global variables
chat_workers: Dict[int, worker.Worker] = {}
user_cfg = None
engine = None

async def start_command(update: telegram.Update, context):
    """Handler for /start command"""
    try:
        if not update.message or not update.message.chat or update.message.chat.type != "private":
            await update.message.reply_text(context.bot_data["default_loc"].get("error_nonprivate_chat"))
            return

        chat_id = update.message.chat_id
        log.info(f"Received /start from: {chat_id}")
        
        # Check if a worker already exists for that chat
        old_worker = chat_workers.get(chat_id)
        # If it exists, gracefully stop the worker
        if old_worker:
            log.debug(f"Received request to stop {old_worker.name}")
            old_worker.stop("request")

        # Initialize a new worker for the chat
        new_worker = worker.Worker(
            bot=context.bot,
            chat=update.message.chat,
            telegram_user=update.message.from_user,
            cfg=user_cfg,
            engine=engine,
            daemon=True
        )
        
        # Start the worker
        log.debug(f"Starting {new_worker.name}")
        await new_worker.start()
        
        # Store the worker in the dictionary
        chat_workers[chat_id] = new_worker
    except Exception as e:
        log.error(f"Error in start_command: {str(e)}")
        log.error(traceback.format_exc())
        try:
            await update.message.reply_text("An error occurred while starting the bot. Please try again later.")
        except:
            pass
        raise

async def message_handler(update: telegram.Update, context):
    """Handler for all non-command messages"""
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

async def callback_query_handler(update: telegram.Update, context):
    """Handler for inline keyboard callbacks"""
    try:
        if not update.callback_query:
            return

        log.debug(f"Received callback query: {update.callback_query.data}")
        receiving_worker = chat_workers.get(update.callback_query.from_user.id)
        
        if receiving_worker is None:
            await update.callback_query.message.reply_text(
                context.bot_data["default_loc"].get("error_no_worker_for_chat")
            )
            return

        if update.callback_query.data == "cmd_cancel":
            log.debug(f"Forwarding CancelSignal to {receiving_worker}")
            receiving_worker.queue.put(worker.CancelSignal())
            await update.callback_query.answer()
        else:
            log.debug(f"Forwarding callback query to {receiving_worker}")
            receiving_worker.queue.put(update)
    except Exception as e:
        log.error(f"Error in callback_query_handler: {str(e)}")
        log.error(traceback.format_exc())
        try:
            await update.callback_query.answer("An error occurred. Please try again.")
        except:
            pass
        raise

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

def main():
    """Start the bot."""
    global user_cfg, engine

    try:
        # Load config
        log.debug("Loading config file...")
        config_path = os.environ.get("CONFIG_PATH", "config/config.toml")
        
        # If the config file does not exist, clone the template and exit
        if not os.path.isfile(config_path):
            log.debug("config/config.toml does not exist.")
            template_path = "config/template_config.toml"
            if not os.path.isfile(template_path):
                log.fatal(f"Template config file not found at {template_path}")
                sys.exit(1)
                
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            with open(template_path, encoding="utf8") as template_cfg_file, \
                    open(config_path, "w", encoding="utf8") as user_cfg_file:
                user_cfg_file.write(template_cfg_file.read())
            log.fatal("A config file has been created. Customize it, then restart greed!")
            sys.exit(1)

        # Load and validate the config
        log.debug(f"Reading config from {config_path}")
        with open(config_path, encoding="utf8") as cfg_file:
            user_cfg = nuconfig.NuConfig(cfg_file)

        # Create the database engine
        log.debug("Creating the sqlalchemy engine...")
        db_engine = os.environ.get("DB_ENGINE", user_cfg["Database"]["engine"])
        log.debug(f"Using database engine: {db_engine}")
        engine = create_engine(db_engine, echo=True)  # Enable SQL query logging

        # Create all tables
        log.debug("Creating all missing tables...")
        db.TableDeclarativeBase.metadata.create_all(engine)

        # Create the application and pass it your bot's token
        token = user_cfg["Telegram"]["token"]
        if not token:
            log.fatal("No bot token found in config file!")
            sys.exit(1)
            
        log.debug("Creating Telegram application...")
        application = Application.builder().token(token).build()

        # Default localization
        default_language = user_cfg["Language"]["default_language"]
        log.debug(f"Setting up localization with default language: {default_language}")
        application.bot_data["default_loc"] = localization.Localization(
            language=default_language,
            fallback=default_language
        )

        # Add handlers
        log.debug("Adding command handlers...")
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
            message_handler
        ))
        application.add_handler(CallbackQueryHandler(callback_query_handler))
        application.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))

        # Add error handler
        application.add_error_handler(error_handler)

        # Start the Bot
        log.info("Starting bot...")
        application.run_polling(allowed_updates=telegram.Update.ALL_TYPES)
        
    except Exception as e:
        log.error(f"Fatal error in main: {str(e)}")
        log.error(traceback.format_exc())
        sys.exit(1)

if __name__ == "__main__":
    main()
