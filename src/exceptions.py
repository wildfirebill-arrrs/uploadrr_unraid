class LoginException(Exception):
    def __init__(self, *args, **kwargs):
        default_message = "An error occured while logging in"
        # if any arguments are passed...
        # If you inherit from the exception that takes message as a keyword
        # maybe you will need to check kwargs here
        if args:
            # ... pass them to the super constructor
            super().__init__(*args, **kwargs)
        else:  # else, the exception was raised without arguments ...
            # ... pass the default message to the super constructor
            super().__init__(default_message, **kwargs)


class UploadException(Exception):
    def __init__(self, *args, **kwargs):
        default_message = "An error occured while uploading"
        # if any arguments are passed...
        # If you inherit from the exception that takes message as a keyword
        # maybe you will need to check kwargs here
        if args:
            # ... pass them to the super constructor
            super().__init__(*args, **kwargs)
        else:  # else, the exception was raised without arguments ...
            # ... pass the default message to the super constructor
            super().__init__(default_message, **kwargs)


class BDInfoExtractionError(Exception):
    """Raised when BDInfo fails to extract information from a disc."""

    def __init__(self, *args, **kwargs):
        default_message = "Failed to extract BDInfo. Possibly due to corrupted or incomplete BDMV structure."
        if args:
            super().__init__(*args, **kwargs)
        else:
            super().__init__(default_message, **kwargs)


class FileTooLargeError(UploadException):
    """Raised when an image exceeds the host's file size limit."""

    def __init__(self, *args, **kwargs):
        default_message = "File exceeds the host size limit"
        if args:
            super().__init__(*args, **kwargs)
        else:
            super().__init__(default_message, **kwargs)


class ApiKeyFormatError(UploadException):
    """Raised when an image host rejects an API key due to an unsupported format."""

    def __init__(self, *args, **kwargs):
        default_message = "API key format is no longer supported by the image host"
        if args:
            super().__init__(*args, **kwargs)
        else:
            super().__init__(default_message, **kwargs)
