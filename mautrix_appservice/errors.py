# -*- coding: future_fstrings -*-
# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2018 Tulir Asokan
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.


class MatrixError(Exception):
    """A generic Matrix error. Specific errors will subclass this."""
    pass


class IntentError(MatrixError):
    def __init__(self, message, source):
        super().__init__(message)
        self.source = source


class MatrixRequestError(MatrixError):
    """ The home server returned an error response. """

    def __init__(self, code=0, text="", errcode=None, message=None):
        super().__init__(f"{code}: {text}")
        self.code = code
        self.text = text
        self.errcode = errcode
        self.message = message
