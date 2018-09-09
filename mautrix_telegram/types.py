from typing import Dict, NewType

MatrixUserID = NewType('MatrixUserID', str)
MatrixRoomID = NewType('MatrixRoomID', str)
MatrixEventID = NewType('MatrixEventID', str)

MatrixEvent = NewType('MatrixEvent', Dict)

TelegramID = NewType('TelegramID', int)
