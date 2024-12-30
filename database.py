import datetime
import logging
from typing import *
import uuid

import requests
import telegram
from sqlalchemy import ForeignKey, UniqueConstraint, Integer, BigInteger, String, Text, LargeBinary, DateTime, Boolean
from sqlalchemy.orm import Session, DeclarativeBase, Mapped, mapped_column, relationship, backref
from datetime import datetime

import utils

if TYPE_CHECKING:
    import worker

log = logging.getLogger(__name__)

# Create a base class to define all the database subclasses
class TableDeclarativeBase(DeclarativeBase):
    pass

class User(TableDeclarativeBase):
    """A Telegram user who uses the bot."""
    
    __tablename__ = "users"
    
    # Telegram data
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    first_name: Mapped[str] = mapped_column(String)
    last_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    username: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Current language
    language: Mapped[str] = mapped_column(String, default="en")  # Default to English
    # Credit
    credit: Mapped[int] = mapped_column(Integer, default=0)
    
    def __init__(self, w=None, **kwargs):
        super().__init__(**kwargs)
        if w is not None:
            self.user_id = w.telegram_user.id
            self.first_name = w.telegram_user.first_name
            self.last_name = w.telegram_user.last_name
            self.username = w.telegram_user.username
            # Set language from user's Telegram client if available
            self.language = w.telegram_user.language_code if w.telegram_user.language_code in w.cfg["Language"]["enabled_languages"] else w.cfg["Language"]["default_language"]
    # Extra table parameters
    __table_args__ = (UniqueConstraint("user_id"),)

class Product(TableDeclarativeBase):
    """A purchasable product"""
    
    __tablename__ = "products"
    
    # The product ID
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # The product name
    name: Mapped[str] = mapped_column(String)
    # The product description
    description: Mapped[str] = mapped_column(Text)
    # The product image
    image: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    # The product price
    price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # The product deleted flag
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)

class Transaction(TableDeclarativeBase):
    """A greed wallet transaction"""
    
    __tablename__ = "transactions"
    
    # The transaction ID
    transaction_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # The user who created the transaction
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id"))
    user = relationship("User", backref=backref("transactions"))
    # The transaction value
    value: Mapped[int] = mapped_column(Integer)
    # The transaction provider
    provider: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # The transaction provider ID
    provider_charge_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # The transaction Telegram payment ID
    telegram_charge_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # The transaction creation date
    creation_date: Mapped[datetime] = mapped_column(DateTime)
    # The refund status
    refunded: Mapped[bool] = mapped_column(Boolean, default=False)
    # The order associated to this transaction
    order_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("orders.order_id"), nullable=True)
    order = relationship("Order", backref=backref("transaction", uselist=False))

class Order(TableDeclarativeBase):
    """A list of product items"""
    
    __tablename__ = "orders"
    
    # The unique order ID
    order_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # The user who created the order
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id"))
    user = relationship("User", backref=backref("orders"))
    # The order creation date
    creation_date: Mapped[datetime] = mapped_column(DateTime)
    # The order delivery date
    delivery_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # The order shipping date
    shipping_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # The order status
    status: Mapped[str] = mapped_column(String, default="NEW")
    # The order notes
    notes: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # The order items
    items = relationship("OrderItem", backref=backref("order"))

class OrderItem(TableDeclarativeBase):
    """A product item in an order"""
    
    __tablename__ = "order_items"
    
    # The item ID
    item_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # The product ID
    product_id: Mapped[int] = mapped_column(Integer, ForeignKey("products.id"))
    product = relationship("Product", backref=backref("order_items"))
    # The order ID
    order_id: Mapped[int] = mapped_column(Integer, ForeignKey("orders.order_id"))
    # The quantity
    quantity: Mapped[int] = mapped_column(Integer, default=1)

class Admin(TableDeclarativeBase):
    """An administrator of the bot"""
    
    __tablename__ = "admins"
    
    # The admin user ID
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id"), primary_key=True)
    user = relationship("User", backref=backref("admin_info", uselist=False))
    # Admin permissions
    edit_products: Mapped[bool] = mapped_column(Boolean, default=False)
    receive_orders: Mapped[bool] = mapped_column(Boolean, default=False)
    create_transactions: Mapped[bool] = mapped_column(Boolean, default=False)
    display_on_help: Mapped[bool] = mapped_column(Boolean, default=False)
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False)
    live_mode: Mapped[bool] = mapped_column(Boolean, default=False)
