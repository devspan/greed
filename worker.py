import os
import sys
import datetime
import logging
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
import localization
import nuconfig
from utils.menu_state import MenuState, MenuManager

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

    def __init__(self,
                 bot,
                 chat: telegram.Chat,
                 telegram_user: telegram.User,
                 cfg: dict,
                 engine,
                 *args, **kwargs):
        # Initialize the thread
        self.name = f"Worker {chat.id}"
        # Store the bot instance
        self.bot = bot
        # Store the chat where this worker is assigned to
        self.chat = chat
        # Store the Telegram user who is using the bot
        self.telegram_user = telegram_user
        # Store the config dictionary
        self.cfg = cfg
        # Create a new database session
        log.debug(f"Opening new database session for {self.name}")
        self.session = Session(bind=engine)
        # Get the user db data from the users & admin tables
        self.user = self.session.query(db.User).filter(db.User.user_id == telegram_user.id).one_or_none()
        self.admin = self.session.query(db.Admin).filter(db.Admin.user_id == telegram_user.id).one_or_none()
        # This is a queue of updates to send to the worker
        self.queue = queuem.Queue()
        # The stop flag is checked to determine if the worker should be stopped
        self._should_stop = False
        # The current active invoice payload; reject all invoices with a different payload
        self.invoice_payload = None
        # The stop reason will be displayed to the user when the worker stops
        self._stop_reason = "unknown"
        self.menu_manager = MenuManager()
        self.menu_manager.set_state(MenuState.MAIN)

    def __repr__(self):
        return f"<{self.__class__.__qualname__} {self.name}>"

    def __create_localization(self):
        """Create a localization object based on the user's language."""
        self.loc = localization.Localization(
            language=self.user.language,
            fallback=self.cfg["Language"]["default_language"],
            replacements={"cart_emoji": self.cfg["Appearance"]["cart_emoji"],
                         "currency_symbol": self.cfg["Payments"]["currency_symbol"]}
        )

    async def start(self):
        """Start the worker thread."""
        await self.run()

    async def run(self):
        """The conversation code."""
        log.debug("Starting conversation")
        # Welcome the user to the bot
        try:
            # If the user isn't registered, create a new record and add it to the db
            if self.user is None:
                # Create the user
                self.user = db.User(user_id=self.telegram_user.id,
                                  first_name=self.telegram_user.first_name,
                                  last_name=self.telegram_user.last_name,
                                  username=self.telegram_user.username,
                                  language=self.cfg["Language"]["default_language"])
                # Add the created user to the database
                self.session.add(self.user)
                # Commit the transaction
                self.session.commit()
                # Create the localization object
                self.__create_localization()
                # Send the welcome message
                await self.bot.send_message(self.chat.id, self.loc.get("conversation_after_start"))
            # If the user is registered, update his data
            else:
                # Update the user's information
                self.user.first_name = self.telegram_user.first_name
                self.user.last_name = self.telegram_user.last_name
                self.user.username = self.telegram_user.username
                # Commit the transaction
                self.session.commit()
                # Create the localization object
                self.__create_localization()
                # User exists
                log.debug("Resuming previous conversation")
                log.debug(f"User's language is {self.user.language}")
            # If the user is not an admin, send him to the user menu
            if self.admin is None:
                # Open the user menu
                await self.__user_menu()
            # If the user is an admin, send him to the admin menu
            else:
                # Open the admin menu
                await self.__admin_menu()
        except Exception as e:
            # Try to notify the user of the exception
            try:
                await self.bot.send_message(self.chat.id, self.loc.get("fatal_conversation_exception"))
            except Exception as ne:
                log.error(f"Failed to notify the user of a conversation exception: {ne}")
            log.error(f"Exception in {self}: {e}")
            traceback.print_exception(*sys.exc_info())
        # Notify the user that the session has been closed
        try:
            await self.bot.send_message(self.chat.id, self.loc.get("conversation_closed"))
        except Exception as e:
            log.error(f"Failed to notify the user that the session was closed: {e}")
        # Close the database session
        self.session.close()
        log.debug("Conversation ended")
        return 0

    def is_ready(self):
        """Check if the worker is ready to start its work."""
        return self.cfg is not None and self.session is not None

    def stop(self, reason: str = ""):
        """Gracefully stop the worker process"""
        # Send a stop message to the thread
        self._stop_reason = reason
        self._should_stop = True

    def update_user(self) -> db.User:
        """Update the user data."""
        log.debug("Fetching updated user data from the database")
        self.user = self.session.query(db.User).filter(db.User.user_id == self.chat.id).one_or_none()
        return self.user

    # noinspection PyUnboundLocalVariable
    async def __receive_next_update(self) -> telegram.Update:
        """Get the next update from the queue.
        If no update is found, block the process until one is received.
        If a stop signal is sent, try to gracefully stop the thread."""
        # Pop data from the queue
        try:
            data = self.queue.get(timeout=self.cfg["Telegram"]["conversation_timeout"])
        except queuem.Empty:
            # If the conversation times out, gracefully stop the thread
            await self.__graceful_stop(StopSignal("timeout"))
        # Check if the data is a stop signal instance
        if isinstance(data, StopSignal):
            # Gracefully stop the process
            await self.__graceful_stop(data)
        # Return the received update
        return data

    async def __wait_for_specific_message(self,
                                    items: List[str],
                                    cancellable: bool = False) -> Union[telegram.Update, CancelSignal]:
        """Continue getting updates until one of the strings contained in the list is received
        as a message. Returns the full update object."""
        log.debug("Waiting for a specific message...")
        while True:
            # Get the next update
            update = await self.__receive_next_update()
            # If a CancelSignal is received...
            if isinstance(update, CancelSignal):
                # And the wait is cancellable...
                if cancellable:
                    # Return the CancelSignal
                    return update
                else:
                    # Ignore the signal
                    continue
            # Ensure the update contains a message
            if update.message is None:
                continue
            # Ensure the message contains text
            if update.message.text is None:
                continue
            # Check if the message is contained in the list
            if update.message.text not in items:
                continue
            # Return the full update object
            return update

    async def __wait_for_regex(self, regex: str, cancellable: bool = False) -> Union[str, CancelSignal]:
        """Continue getting updates until the regex finds a match in a message, then return the first capture group."""
        log.debug("Waiting for a regex...")
        while True:
            # Get the next update
            update = await self.__receive_next_update()
            # If a CancelSignal is received...
            if isinstance(update, CancelSignal):
                # And the wait is cancellable...
                if cancellable:
                    # Return the CancelSignal
                    return update
                else:
                    # Ignore the signal
                    continue
            # Ensure the update contains a message
            if update.message is None:
                continue
            # Ensure the message contains text
            if update.message.text is None:
                continue
            # Try to match the regex with the received message
            match = re.search(regex, update.message.text, re.DOTALL)
            if match is None:
                continue
            # Return the first capture group
            return match.group(1)

    async def __wait_for_precheckoutquery(self,
                                    cancellable: bool = False) -> Union[telegram.PreCheckoutQuery, CancelSignal]:
        """Continue getting updates until a precheckoutquery is received.
        If the cancellable parameter is True, CancelSignal can be returned."""
        log.debug("Waiting for a PreCheckoutQuery...")
        while True:
            # Get the next update
            update = await self.__receive_next_update()
            # If a CancelSignal is received...
            if isinstance(update, CancelSignal):
                # And the wait is cancellable...
                if cancellable:
                    # Return the CancelSignal
                    return update
                else:
                    # Ignore the signal
                    continue
            # Ensure the update contains a precheckoutquery
            if update.pre_checkout_query is None:
                continue
            # Return the precheckoutquery
            return update.pre_checkout_query

    async def __wait_for_successfulpayment(self,
                                     cancellable: bool = False) -> Union[telegram.SuccessfulPayment, CancelSignal]:
        """Continue getting updates until a successfulpayment is received."""
        log.debug("Waiting for a SuccessfulPayment...")
        while True:
            # Get the next update
            update = await self.__receive_next_update()
            # If a CancelSignal is received...
            if isinstance(update, CancelSignal):
                # And the wait is cancellable...
                if cancellable:
                    # Return the CancelSignal
                    return update
                else:
                    # Ignore the signal
                    continue
            # Ensure the update contains a message
            if update.message is None:
                continue
            # Ensure the message is a successfulpayment
            if update.message.successful_payment is None:
                continue
            # Return the successfulpayment
            return update.message.successful_payment

    async def __wait_for_photo(self, cancellable: bool = False) -> Union[List[telegram.PhotoSize], CancelSignal]:
        """Continue getting updates until a photo is received, then return it."""
        log.debug("Waiting for a photo...")
        while True:
            # Get the next update
            update = await self.__receive_next_update()
            # If a CancelSignal is received...
            if isinstance(update, CancelSignal):
                # And the wait is cancellable...
                if cancellable:
                    # Return the CancelSignal
                    return update
                else:
                    # Ignore the signal
                    continue
            # Ensure the update contains a message
            if update.message is None:
                continue
            # Ensure the message contains a photo
            if update.message.photo is None:
                continue
            # Return the photo array
            return update.message.photo

    async def __wait_for_inlinekeyboard_callback(self, cancellable: bool = False) \
            -> Union[telegram.CallbackQuery, CancelSignal]:
        """Continue getting updates until an inline keyboard callback is received, then return it."""
        log.debug("Waiting for a CallbackQuery...")
        while True:
            # Get the next update
            update = await self.__receive_next_update()
            # If a CancelSignal is received...
            if isinstance(update, CancelSignal):
                # And the wait is cancellable...
                if cancellable:
                    # Return the CancelSignal
                    return update
                else:
                    # Ignore the signal
                    continue
            # Ensure the update is a CallbackQuery
            if update.callback_query is None:
                continue
            # Answer the callbackquery
            self.bot.answer_callback_query(update.callback_query.id)
            # Return the callbackquery
            return update.callback_query

    async def __user_menu(self):
        """Display the user menu."""
        # Create a keyboard with the user menu
        keyboard = [[
            telegram.KeyboardButton(self.loc.get("menu_order")),
            telegram.KeyboardButton(self.loc.get("menu_order_status"))
        ], [
            telegram.KeyboardButton(self.loc.get("menu_add_credit")),
            telegram.KeyboardButton(self.loc.get("menu_language"))
        ], [
            telegram.KeyboardButton(self.loc.get("menu_help")),
            telegram.KeyboardButton(self.loc.get("menu_bot_info"))
        ]]
        
        # Send the previously created keyboard to the user
        await self.bot.send_message(
            chat_id=self.chat.id,
            text=self.loc.get("conversation_open_user_menu"),
            reply_markup=telegram.ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
        )
        
        # Wait for a reply from the user
        update = await self.__wait_for_specific_message(
            [self.loc.get("menu_order"), self.loc.get("menu_order_status"),
             self.loc.get("menu_add_credit"), self.loc.get("menu_language"),
             self.loc.get("menu_help"), self.loc.get("menu_bot_info")],
            cancellable=True
        )
        
        # If the user has selected an option...
        if update.message is not None:
            option = update.message.text
            # Order
            if option == self.loc.get("menu_order"):
                await self.__order_menu()
            # Order status
            elif option == self.loc.get("menu_order_status"):
                await self.__order_status()
            # Add credit
            elif option == self.loc.get("menu_add_credit"):
                await self.__add_credit_menu()
            # Language
            elif option == self.loc.get("menu_language"):
                await self.__language_menu()
            # Help
            elif option == self.loc.get("menu_help"):
                await self.__help_menu()
            # Bot info
            elif option == self.loc.get("menu_bot_info"):
                await self.__bot_info()
            # Easter egg
            elif option == "⚙️ Bot maintenance":
                await self.__bot_maintenance()

    async def __order_menu(self):
        """User menu to order products from the shop."""
        log.debug("Displaying __order_menu")
        # Get the products list from the db
        products = self.session.query(db.Product).filter_by(deleted=False).all()
        # Create a dict to be used as 'cart'
        # The key is the message id of the product list
        cart: Dict[List[db.Product, int]] = {}
        # Initialize the products list
        for product in products:
            # If the product is not for sale, don't display it
            if product.price is None:
                continue
            # Send the message without the keyboard to get the message id
            message = await product.send_as_message(w=self, chat_id=self.chat.id)
            # Add the product to the cart
            cart[message['message_id']] = [product, 0]
            # Create the inline keyboard to add the product to the cart
            inline_keyboard = telegram.InlineKeyboardMarkup(
                [[telegram.InlineKeyboardButton(self.loc.get("menu_add_to_cart"), callback_data="cart_add")]]
            )
            # Edit the sent message and add the inline keyboard
            if product.image is None:
                await self.bot.edit_message_text(chat_id=self.chat.id,
                                           message_id=message['message_id'],
                                           text=product.text(w=self),
                                           reply_markup=inline_keyboard)
            else:
                await self.bot.edit_message_caption(chat_id=self.chat.id,
                                              message_id=message['message_id'],
                                              caption=product.text(w=self),
                                              reply_markup=inline_keyboard)
        # Create the keyboard with the cancel button
        inline_keyboard = telegram.InlineKeyboardMarkup([[telegram.InlineKeyboardButton(self.loc.get("menu_cancel"),
                                                                                        callback_data="cart_cancel")]])
        # Send a message containing the button to cancel or pay
        final_msg = await self.bot.send_message(self.chat.id,
                                          self.loc.get("conversation_cart_actions"),
                                          reply_markup=inline_keyboard)
        # Wait for user input
        while True:
            callback = await self.__wait_for_inlinekeyboard_callback()
            # React to the user input
            # If the cancel button has been pressed...
            if callback.data == "cart_cancel":
                # Stop waiting for user input and go back to the previous menu
                return
            # If a Add to Cart button has been pressed...
            elif callback.data == "cart_add":
                # Get the selected product, ensuring it exists
                p = cart.get(callback.message.message_id)
                if p is None:
                    continue
                product = p[0]
                # Add 1 copy to the cart
                cart[callback.message.message_id][1] += 1
                # Create the product inline keyboard
                product_inline_keyboard = telegram.InlineKeyboardMarkup(
                    [
                        [telegram.InlineKeyboardButton(self.loc.get("menu_add_to_cart"),
                                                       callback_data="cart_add"),
                         telegram.InlineKeyboardButton(self.loc.get("menu_remove_from_cart"),
                                                       callback_data="cart_remove")]
                    ])
                # Create the final inline keyboard
                final_inline_keyboard = telegram.InlineKeyboardMarkup(
                    [
                        [telegram.InlineKeyboardButton(self.loc.get("menu_cancel"), callback_data="cart_cancel")],
                        [telegram.InlineKeyboardButton(self.loc.get("menu_done"), callback_data="cart_done")]
                    ])
                # Edit both the product and the final message
                if product.image is None:
                    await self.bot.edit_message_text(chat_id=self.chat.id,
                                               message_id=callback.message.message_id,
                                               text=product.text(w=self,
                                                                 cart_qty=cart[callback.message.message_id][1]),
                                               reply_markup=product_inline_keyboard)
                else:
                    await self.bot.edit_message_caption(chat_id=self.chat.id,
                                                  message_id=callback.message.message_id,
                                                  caption=product.text(w=self,
                                                                       cart_qty=cart[callback.message.message_id][1]),
                                                  reply_markup=product_inline_keyboard)

                await self.bot.edit_message_text(
                    chat_id=self.chat.id,
                    message_id=final_msg.message_id,
                    text=self.loc.get("conversation_confirm_cart",
                                      product_list=self.__get_cart_summary(cart),
                                      total_cost=str(self.__get_cart_value(cart))),
                    reply_markup=final_inline_keyboard)
            # If the Remove from cart button has been pressed...
            elif callback.data == "cart_remove":
                # Get the selected product, ensuring it exists
                p = cart.get(callback.message.message_id)
                if p is None:
                    continue
                product = p[0]
                # Remove 1 copy from the cart
                if cart[callback.message.message_id][1] > 0:
                    cart[callback.message.message_id][1] -= 1
                else:
                    continue
                # Create the product inline keyboard
                product_inline_list = [[telegram.InlineKeyboardButton(self.loc.get("menu_add_to_cart"),
                                                                      callback_data="cart_add")]]
                if cart[callback.message.message_id][1] > 0:
                    product_inline_list[0].append(telegram.InlineKeyboardButton(self.loc.get("menu_remove_from_cart"),
                                                                                callback_data="cart_remove"))
                product_inline_keyboard = telegram.InlineKeyboardMarkup(product_inline_list)
                # Create the final inline keyboard
                final_inline_list = [[telegram.InlineKeyboardButton(self.loc.get("menu_cancel"),
                                                                    callback_data="cart_cancel")]]
                for product_id in cart:
                    if cart[product_id][1] > 0:
                        final_inline_list.append([telegram.InlineKeyboardButton(self.loc.get("menu_done"),
                                                                                callback_data="cart_done")])
                        break
                final_inline_keyboard = telegram.InlineKeyboardMarkup(final_inline_list)
                # Edit the product message
                if product.image is None:
                    await self.bot.edit_message_text(chat_id=self.chat.id, message_id=callback.message.message_id,
                                               text=product.text(w=self,
                                                                 cart_qty=cart[callback.message.message_id][1]),
                                               reply_markup=product_inline_keyboard)
                else:
                    await self.bot.edit_message_caption(chat_id=self.chat.id,
                                                  message_id=callback.message.message_id,
                                                  caption=product.text(w=self,
                                                                       cart_qty=cart[callback.message.message_id][1]),
                                                  reply_markup=product_inline_keyboard)

                await self.bot.edit_message_text(
                    chat_id=self.chat.id,
                    message_id=final_msg.message_id,
                    text=self.loc.get("conversation_confirm_cart",
                                      product_list=self.__get_cart_summary(cart),
                                      total_cost=str(self.__get_cart_value(cart))),
                    reply_markup=final_inline_keyboard)
            # If the done button has been pressed...
            elif callback.data == "cart_done":
                # End the loop
                break
        # Create an inline keyboard with a single skip button
        cancel = telegram.InlineKeyboardMarkup([[telegram.InlineKeyboardButton(self.loc.get("menu_skip"),
                                                                               callback_data="cmd_cancel")]])
        # Ask if the user wants to add notes to the order
        await self.bot.send_message(self.chat.id, self.loc.get("ask_order_notes"), reply_markup=cancel)
        # Wait for user input
        notes = await self.__wait_for_regex(r"(.*)", cancellable=True)
        # Create a new Order
        order = db.Order(user=self.user,
                         creation_date=datetime.datetime.now(),
                         notes=notes if not isinstance(notes, CancelSignal) else "")
        # Add the record to the session and get an ID
        self.session.add(order)
        # For each product added to the cart, create a new OrderItem
        for product in cart:
            # Create {quantity} new OrderItems
            for i in range(0, cart[product][1]):
                order_item = db.OrderItem(product=cart[product][0],
                                          order=order)
                self.session.add(order_item)
        # Ensure the user has enough credit to make the purchase
        credit_required = self.__get_cart_value(cart) - self.user.credit
        # Notify user in case of insufficient credit
        if credit_required > 0:
            await self.bot.send_message(self.chat.id, self.loc.get("error_not_enough_credit"))
            # Suggest payment for missing credit value if configuration allows refill
            if self.cfg["Payments"]["CreditCard"]["credit_card_token"] != "" \
                    and self.cfg["Appearance"]["refill_on_checkout"] \
                    and Price(self.cfg["Payments"]["CreditCard"]["min_amount"], worker=self) <= \
                    credit_required <= \
                    Price(self.cfg["Payments"]["CreditCard"]["max_amount"], worker=self):
                await self.__make_payment(Price(credit_required, worker=self))
        # If afer requested payment credit is still insufficient (either payment failure or cancel)
        if self.user.credit < self.__get_cart_value(cart):
            # Rollback all the changes
            self.session.rollback()
        else:
            # User has credit and valid order, perform transaction now
            await self.__order_transaction(order=order, value=-int(self.__get_cart_value(cart)))

    def __get_cart_value(self, cart):
        # Calculate total items value in cart
        value = Price(0, worker=self)
        for product in cart:
            value += cart[product][0].price * cart[product][1]
        return value

    def __get_cart_summary(self, cart):
        # Create the cart summary
        product_list = ""
        for product_id in cart:
            if cart[product_id][1] > 0:
                product_list += cart[product_id][0].text(w=self,
                                                         style="short",
                                                         cart_qty=cart[product_id][1]) + "\n"
        return product_list

    async def __order_transaction(self, order, value):
        # Create a new transaction and add it to the session
        transaction = db.Transaction(user=self.user,
                                     value=value,
                                     order=order)
        self.session.add(transaction)
        # Commit all the changes
        self.session.commit()
        # Update the user's credit
        self.user.recalculate_credit()
        # Commit all the changes
        self.session.commit()
        # Notify admins about new transation
        await self.__order_notify_admins(order=order)

    async def __order_notify_admins(self, order):
        # Notify the user of the order result
        await self.bot.send_message(self.chat.id, self.loc.get("success_order_created", order=order.text(w=self,
                                                                                                   user=True)))
        # Notify the admins (in Live Orders mode) of the new order
        admins = self.session.query(db.Admin).filter_by(live_mode=True).all()
        # Create the order keyboard
        order_keyboard = telegram.InlineKeyboardMarkup(
            [
                [telegram.InlineKeyboardButton(self.loc.get("menu_complete"), callback_data="order_complete")],
                [telegram.InlineKeyboardButton(self.loc.get("menu_refund"), callback_data="order_refund")]
            ])
        # Notify them of the new placed order
        for admin in admins:
            await self.bot.send_message(admin.user_id,
                                  self.loc.get('notification_order_placed',
                                               order=order.text(w=self)),
                                  reply_markup=order_keyboard)

    async def __order_status(self):
        """Display the status of the sent orders."""
        log.debug("Displaying __order_status")
        # Find the latest orders
        orders = self.session.query(db.Order) \
            .filter(db.Order.user == self.user) \
            .order_by(db.Order.creation_date.desc()) \
            .limit(20) \
            .all()
        # Ensure there is at least one order to display
        if len(orders) == 0:
            await self.bot.send_message(self.chat.id, self.loc.get("error_no_orders"))
        # Display the order status to the user
        for order in orders:
            await self.bot.send_message(self.chat.id, order.text(w=self, user=True))
        # TODO: maybe add a page displayer instead of showing the latest 5 orders

    async def __add_credit_menu(self):
        """Add more credit to the account."""
        log.debug("Displaying __add_credit_menu")
        # Create a payment methods keyboard
        keyboard = list()
        # Add the supported payment methods to the keyboard
        # Cash
        if self.cfg["Payments"]["Cash"]["enable_pay_with_cash"]:
            keyboard.append([telegram.KeyboardButton(self.loc.get("menu_cash"))])
        # Telegram Payments
        if self.cfg["Payments"]["CreditCard"]["credit_card_token"] != "":
            keyboard.append([telegram.KeyboardButton(self.loc.get("menu_credit_card"))])
        # Keyboard: go back to the previous menu
        keyboard.append([telegram.KeyboardButton(self.loc.get("menu_cancel"))])
        # Send the keyboard to the user
        await self.bot.send_message(self.chat.id, self.loc.get("conversation_payment_method"),
                              reply_markup=telegram.ReplyKeyboardMarkup(keyboard, one_time_keyboard=True))
        # Wait for a reply from the user
        selection = await self.__wait_for_specific_message(
            [self.loc.get("menu_cash"), self.loc.get("menu_credit_card"), self.loc.get("menu_cancel")],
            cancellable=True)
        # If the user has selected the Cash option...
        if selection == self.loc.get("menu_cash") and self.cfg["Payments"]["Cash"]["enable_pay_with_cash"]:
            # Go to the pay with cash function
            await self.bot.send_message(self.chat.id,
                                  self.loc.get("payment_cash", user_cash_id=self.user.identifiable_str()))
        # If the user has selected the Credit Card option...
        elif selection == self.loc.get("menu_credit_card") and self.cfg["Payments"]["CreditCard"]["credit_card_token"]:
            # Go to the pay with credit card function
            await self.__add_credit_cc()
        # If the user has selected the Cancel option...
        elif isinstance(selection, CancelSignal):
            # Send him back to the previous menu
            return

    async def __add_credit_cc(self):
        """Add money to the wallet through a credit card payment."""
        log.debug("Displaying __add_credit_cc")
        # Create a keyboard to be sent later
        presets = self.cfg["Payments"]["CreditCard"]["payment_presets"]
        keyboard = [[telegram.KeyboardButton(str(Price(preset, worker=self)))] for preset in presets]
        keyboard.append([telegram.KeyboardButton(self.loc.get("menu_cancel"))])
        # Boolean variable to check if the user has cancelled the action
        cancelled = False
        # Loop used to continue asking if there's an error during the input
        while not cancelled:
            # Send the message and the keyboard
            await self.bot.send_message(self.chat.id, self.loc.get("payment_cc_amount"),
                                  reply_markup=telegram.ReplyKeyboardMarkup(keyboard, one_time_keyboard=True))
            # Wait until a valid amount is sent
            selection = await self.__wait_for_regex(r"([0-9]+(?:[.,][0-9]+)?|" + self.loc.get("menu_cancel") + r")",
                                              cancellable=True)
            # If the user cancelled the action
            if isinstance(selection, CancelSignal):
                # Exit the loop
                cancelled = True
                continue
            # Convert the amount to an integer
            value = Price(selection, worker=self)
            # Ensure the amount is within the range
            if value > Price(self.cfg["Payments"]["CreditCard"]["max_amount"], worker=self):
                await self.bot.send_message(self.chat.id,
                                      self.loc.get("error_payment_amount_over_max",
                                                   max_amount=Price(self.cfg["CreditCard"]["max_amount"], worker=self)))
                continue
            elif value < Price(self.cfg["Payments"]["CreditCard"]["min_amount"], worker=self):
                await self.bot.send_message(self.chat.id,
                                      self.loc.get("error_payment_amount_under_min",
                                                   min_amount=Price(self.cfg["CreditCard"]["min_amount"], worker=self)))
                continue
            break
        # If the user cancelled the action...
        else:
            # Exit the function
            return
        # Issue the payment invoice
        await self.__make_payment(amount=value)

    async def __make_payment(self, amount):
        """Make a payment with the credit card."""
        log.debug("Attempting payment...")
        # Create the price array
        prices = [telegram.LabeledPrice(label=self.loc.get("payment_invoice_label"), amount=int(amount))]
        # If the user has to pay a fee when using the credit card, add it to the prices list
        fee = int(self.__get_total_fee(amount))
        if fee > 0:
            prices.append(telegram.LabeledPrice(label=self.loc.get("payment_invoice_fee_label"),
                                               amount=fee))
        # Create the invoice
        invoice = await self.bot.send_invoice(self.chat.id,
                                       title=self.loc.get("payment_invoice_title"),
                                       description=self.loc.get("payment_invoice_description", amount=str(amount)),
                                       payload=str(uuid.uuid4()),
                                       provider_token=self.cfg["Payments"]["CreditCard"]["credit_card_token"],
                                       start_parameter="tempdeeplink",
                                       currency=self.cfg["Payments"]["currency"],
                                       prices=prices,
                                       need_name=self.cfg["Payments"]["CreditCard"]["name_required"],
                                       need_email=self.cfg["Payments"]["CreditCard"]["email_required"],
                                       need_phone_number=self.cfg["Payments"]["CreditCard"]["phone_required"])
        # Wait for the payment
        payment = await self.__wait_for_successfulpayment()
        # Create a new transaction
        transaction = db.Transaction(user=self.user,
                                    value=int(payment.total_amount),
                                    provider="Credit Card",
                                    telegram_charge_id=payment.telegram_payment_charge_id,
                                    provider_charge_id=payment.provider_payment_charge_id)
        self.session.add(transaction)
        # Update the user's credit
        self.user.recalculate_credit()
        # Commit all the changes
        self.session.commit()
        # Notify the user of the success
        await self.bot.send_message(self.chat.id,
                              self.loc.get("success_transaction_created",
                                           transaction=transaction.text(w=self)))

    def __get_total_fee(self, amount):
        # Calculate a fee for the required amount
        fee_percentage = self.cfg["Payments"]["CreditCard"]["fee_percentage"] / 100
        fee_fixed = self.cfg["Payments"]["CreditCard"]["fee_fixed"]
        total_fee = amount * fee_percentage + fee_fixed
        if total_fee > 0:
            return total_fee
        # Set the fee to 0 to ensure no accidental discounts are applied
        return 0

    async def __admin_menu(self):
        """Function called from the run method when the user is an administrator.
        Administrative bot actions should be placed here."""
        log.debug("Displaying __admin_menu")
        # Loop used to return to the menu after executing a command
        while True:
            # Create a keyboard with the admin main menu based on the admin permissions specified in the db
            keyboard = []
            if self.admin.edit_products:
                keyboard.append([self.loc.get("menu_products")])
            if self.admin.receive_orders:
                keyboard.append([self.loc.get("menu_orders")])
            if self.admin.create_transactions:
                if self.cfg["Payments"]["Cash"]["enable_create_transaction"]:
                    keyboard.append([self.loc.get("menu_edit_credit")])
                keyboard.append([self.loc.get("menu_transactions"), self.loc.get("menu_csv")])
            if self.admin.is_owner:
                keyboard.append([self.loc.get("menu_edit_admins")])
            keyboard.append([self.loc.get("menu_user_mode")])
            # Send the previously created keyboard to the user (ensuring it can be clicked only 1 time)
            await self.bot.send_message(self.chat.id, self.loc.get("conversation_open_admin_menu"),
                                  reply_markup=telegram.ReplyKeyboardMarkup(keyboard, one_time_keyboard=True))
            # Wait for a reply from the user
            selection = self.__wait_for_specific_message([self.loc.get("menu_products"),
                                                          self.loc.get("menu_orders"),
                                                          self.loc.get("menu_user_mode"),
                                                          self.loc.get("menu_edit_credit"),
                                                          self.loc.get("menu_transactions"),
                                                          self.loc.get("menu_csv"),
                                                          self.loc.get("menu_edit_admins")])
            # If the user has selected the Products option and has the privileges to perform the action...
            if selection == self.loc.get("menu_products") and self.admin.edit_products:
                # Open the products menu
                self.__products_menu()
            # If the user has selected the Orders option and has the privileges to perform the action...
            elif selection == self.loc.get("menu_orders") and self.admin.receive_orders:
                # Open the orders menu
                self.__orders_menu()
            # If the user has selected the Transactions option and has the privileges to perform the action...
            elif selection == self.loc.get("menu_edit_credit") and self.admin.create_transactions:
                # Open the edit credit menu
                self.__create_transaction()
            # If the user has selected the User mode option and has the privileges to perform the action...
            elif selection == self.loc.get("menu_user_mode"):
                # Tell the user how to go back to admin menu
                self.bot.send_message(self.chat.id, self.loc.get("conversation_switch_to_user_mode"))
                # Start the bot in user mode
                self.__user_menu()
            # If the user has selected the Add Admin option and has the privileges to perform the action...
            elif selection == self.loc.get("menu_edit_admins") and self.admin.is_owner:
                # Open the edit admin menu
                self.__add_admin()
            # If the user has selected the Transactions option and has the privileges to perform the action...
            elif selection == self.loc.get("menu_transactions") and self.admin.create_transactions:
                # Open the transaction pages
                self.__transaction_pages()
            # If the user has selected the .csv option and has the privileges to perform the action...
            elif selection == self.loc.get("menu_csv") and self.admin.create_transactions:
                # Generate the .csv file
                await self.__transactions_file()

    def __products_menu(self):
        """Display the admin menu to select a product to edit."""
        log.debug("Displaying __products_menu")
        # Get the products list from the db
        products = self.session.query(db.Product).filter_by(deleted=False).all()
        # Create a list of product names
        product_names = [product.name for product in products]
        # Insert at the start of the list the add product option, the remove product option and the Cancel option
        product_names.insert(0, self.loc.get("menu_cancel"))
        product_names.insert(1, self.loc.get("menu_add_product"))
        product_names.insert(2, self.loc.get("menu_delete_product"))
        # Create a keyboard using the product names
        keyboard = [[telegram.KeyboardButton(product_name)] for product_name in product_names]
        # Send the previously created keyboard to the user (ensuring it can be clicked only 1 time)
        self.bot.send_message(self.chat.id, self.loc.get("conversation_admin_select_product"),
                              reply_markup=telegram.ReplyKeyboardMarkup(keyboard, one_time_keyboard=True))
        # Wait for a reply from the user
        selection = self.__wait_for_specific_message(product_names, cancellable=True)
        # If the user has selected the Cancel option...
        if isinstance(selection, CancelSignal):
            # Exit the menu
            return
        # If the user has selected the Add Product option...
        elif selection == self.loc.get("menu_add_product"):
            # Open the add product menu
            self.__edit_product_menu()
        # If the user has selected the Remove Product option...
        elif selection == self.loc.get("menu_delete_product"):
            # Open the delete product menu
            self.__delete_product_menu()
        # If the user has selected a product
        else:
            # Find the selected product
            product = self.session.query(db.Product).filter_by(name=selection, deleted=False).one()
            # Open the edit menu for that specific product
            self.__edit_product_menu(product=product)

    def __edit_product_menu(self, product: Optional[db.Product] = None):
        """Add a product to the database or edit an existing one."""
        log.debug("Displaying __edit_product_menu")
        # Create an inline keyboard with a single skip button
        cancel = telegram.InlineKeyboardMarkup([[telegram.InlineKeyboardButton(self.loc.get("menu_skip"),
                                                                               callback_data="cmd_cancel")]])
        # Ask for the product name until a valid product name is specified
        while True:
            # Ask the question to the user
            self.bot.send_message(self.chat.id, self.loc.get("ask_product_name"))
            # Display the current name if you're editing an existing product
            if product:
                self.bot.send_message(self.chat.id, self.loc.get("edit_current_value", value=escape(product.name)),
                                      reply_markup=cancel)
            # Wait for an answer
            name = self.__wait_for_regex(r"(.*)", cancellable=bool(product))

    async def __language_menu(self):
        """Display the language selection menu."""
        log.debug("Displaying __language_menu")
        # Get the list of enabled languages from the config
        languages = self.cfg["Language"]["enabled_languages"]
        # Create a keyboard with the languages
        keyboard = []
        # Add a button for each language
        for language in languages:
            keyboard.append([telegram.KeyboardButton(language)])
        # Add a cancel button
        keyboard.append([telegram.KeyboardButton(self.loc.get("menu_cancel"))])
        # Send the language selection message
        await self.bot.send_message(
            chat_id=self.chat.id,
            text=self.loc.get("conversation_language_select"),
            reply_markup=telegram.ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
        )
        # Wait for a reply from the user
        selection = await self.__wait_for_specific_message(
            languages + [self.loc.get("menu_cancel")],
            cancellable=True
        )
        # If the user selected cancel or the wait was cancelled
        if isinstance(selection, CancelSignal) or selection == self.loc.get("menu_cancel"):
            return
        # If the user selected a language
        if selection in languages:
            # Set the user's language
            self.user.language = selection
            # Update the user's record in the database
            self.session.commit()
            # Create a new localization object
            self.__create_localization()
            # Notify the user of the language change
            await self.bot.send_message(
                chat_id=self.chat.id,
                text=self.loc.get("conversation_language_changed")
            )
            # Return to the main menu
            await self.__user_menu()

    async def handle_menu_command(self, command: str) -> bool:
        """Handle menu commands and return True if handled"""
        try:
            if command == self.loc.get("menu_order"):
                self.menu_manager.set_state(MenuState.ORDER)
                await self.show_order_menu()
                return True
            elif command == self.loc.get("menu_order_status"):
                self.menu_manager.set_state(MenuState.ORDER_STATUS)
                await self.show_order_status()
                return True
            elif command == self.loc.get("menu_add_credit"):
                self.menu_manager.set_state(MenuState.ADD_CREDIT)
                await self.show_add_credit()
                return True
            elif command == self.loc.get("menu_language"):
                self.menu_manager.set_state(MenuState.LANGUAGE)
                await self.show_language_selection()
                return True
            elif command == self.loc.get("menu_help"):
                self.menu_manager.set_state(MenuState.HELP)
                await self.show_help()
                return True
            elif command == self.loc.get("menu_bot_info"):
                self.menu_manager.set_state(MenuState.BOT_INFO)
                await self.show_bot_info()
                return True
            return False
        except Exception as e:
            logger.error(f"Error handling menu command: {str(e)}")
            return False

    async def process_message(self, update: telegram.Update):
        """Process incoming messages based on current menu state"""
        try:
            message_text = update.message.text.strip()
            
            # Try to handle as menu command first
            if await self.handle_menu_command(message_text):
                return

            # Handle based on current state
            current_state = self.menu_manager.get_state()
            if current_state == MenuState.ORDER:
                await self.handle_order(message_text)
            elif current_state == MenuState.ADD_CREDIT:
                await self.handle_add_credit(message_text)
            elif current_state == MenuState.LANGUAGE:
                await self.handle_language_selection(message_text)
            else:
                # Unknown command in current state
                await self.send_message(
                    self.loc.get("error_invalid_command_in_state"),
                    reply_markup=self.create_main_menu()
                )
        except Exception as e:
            logger.error(f"Error processing message: {str(e)}")
            await self.send_error_message()
