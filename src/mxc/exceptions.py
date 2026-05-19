# ©️ Pasha Hatsune, 2025-2026
# This file is a part of MXC
# 🌐 https://github.com/MxUserBot/MXC-python
# You can redistribute it and/or modify it under the terms of the GNU AGPLv3
# 🔑 https://www.gnu.org/licenses/agpl-3.0.html

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
