from typing import Dict, NewType

# MatrixId = NewType('MatrixId', str)
MatrixUserID = NewType('MatrixUserID', str)
MatrixRoomID = NewType('MatrixRoomID', str)
MatrixEventID = NewType('MatrixEventID', str)

MatrixEvent = NewType('MatrixEvent', Dict)

TelegramID = NewType('TelegramID', int)
