class BotError(Exception):
    """Base exception class for the bot."""
    pass

class SecurityException(BotError):
    """Raised when a security check fails."""
    pass

class DatabaseException(BotError):
    """Raised when a database operation fails."""
    pass

class WorkerException(BotError):
    """Raised when a worker operation fails."""
    pass

class ConfigurationException(BotError):
    """Raised when there is a configuration error."""
    pass

class PaymentException(BotError):
    """Raised when a payment operation fails."""
    pass 