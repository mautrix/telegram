from .from_matrix import (matrix_reply_to_telegram, matrix_to_telegram, matrix_text_to_telegram,
                          init_mx)
from .from_telegram import (telegram_reply_to_matrix, telegram_to_matrix, init_tg)
from ..context import Context


def init(context: Context):
    init_mx(context)
    init_tg(context)
