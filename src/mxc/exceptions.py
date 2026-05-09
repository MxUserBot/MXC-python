class UploadFailed(Exception):
    pass


class CommandRequiresAdmin(Exception):
    pass


class CommandRequiresOwner(Exception):
    pass


class MatrixBotError(Exception):
    pass


class AuthenticationError(MatrixBotError):
    pass


class NetworkError(MatrixBotError):
    pass


class UsageError(Exception):
    pass
