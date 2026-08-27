"""errors.py  -  Canonical exception hierarchy for QECTOR Decoder Workbench.

All modules must import their error types from here instead of redefining
their own. ``QectorError`` derives from ``RuntimeError`` for backward
compatibility with code that caught the original ``backend.QectorError``
(a RuntimeError subclass).
"""


class QectorError(RuntimeError):
    """Base exception class for all QECTOR Decoder Workbench errors."""
    pass

class QectorConfigError(QectorError):
    """Raised for configuration errors, missing paths, or invalid schemas."""
    pass

class QectorAuthError(QectorError):
    """Raised for Entra ID authentication and token management failures."""
    pass

class QectorDecoderError(QectorError):
    """Raised for syndrome parsing, matrix validation, or solver execution failures."""
    pass

class QectorHardwareError(QectorError):
    """Raised for CUDA / OpenCL device enumeration or driver execution errors."""
    pass

class QectorEgressBlockedError(QectorError):
    """Raised when an illegal outbound network connection is intercepted in air-gap mode."""
    pass

class QectorSecurityError(QectorError):
    """Raised for path traversal attempts, missing cryptographic signatures, or clock tamper detection."""
    pass
