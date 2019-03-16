from typing import Tuple
from unittest.mock import Mock

import pytest
from _pytest.fixtures import FixtureRequest
from pytest_mock import MockFixture

import mautrix_telegram.commands.handler
from mautrix_telegram.commands.handler import (CommandEvent, CommandHandler, CommandProcessor,
                                               HelpSection)
from mautrix_telegram.config import Config
from mautrix_telegram.context import Context
from mautrix_telegram.types import MatrixEventID, MatrixRoomID, MatrixUserID
import mautrix_telegram.user as u

from tests.utils.helpers import AsyncMock, list_true_once_each


@pytest.fixture
def context(request: FixtureRequest) -> Context:
    """Returns a Context with mocked Attributes.

    Uses the attribute cls.config as Config.
    """
    # Config(path, registration_path, base_path)
    config = getattr(request.cls, 'config', Config("", "", ""))
    return Context(az=Mock(), config=config, loop=Mock(), session_container=Mock(), bot=Mock())


@pytest.fixture
def command_processor(context: Context) -> CommandProcessor:
    """Returns a mocked CommandProcessor."""
    return CommandProcessor(context)


class TestCommandEvent:
    config = Config("", "", "")
    config["bridge.command_prefix"] = "tg"
    config["bridge.permissions"] = {"*": "noperm"}

    def test_reply(
        self, command_processor: CommandProcessor, mocker: MockFixture
    ) -> None:
        mocker.patch("mautrix_telegram.user.config", self.config)

        evt = CommandEvent(
            processor=command_processor,
            room=MatrixRoomID("#mock_room:example.org"),
            event=MatrixEventID("$H45H:example.org"),
            sender=u.User(MatrixUserID("@sender:example.org")),
            command="help",
            args=[],
            is_management=True,
            is_portal=False,
        )

        mock_az = command_processor.az

        message = "**This** <i>was</i><br/><strong>all</strong>fun*!"

        # html, no markdown
        evt.reply(message, allow_html=True, render_markdown=False)
        mock_az.intent.send_notice.assert_called_with(
            MatrixRoomID("#mock_room:example.org"),
            "**This** <i>was</i><br/><strong>all</strong>fun*!",
            html="**This** <i>was</i><br/><strong>all</strong>fun*!\n",
        )

        # html, markdown (default)
        evt.reply(message, allow_html=True, render_markdown=True)
        mock_az.intent.send_notice.assert_called_with(
            MatrixRoomID("#mock_room:example.org"),
            "**This** <i>was</i><br/><strong>all</strong>fun*!",
            html=(
                "<p><strong>This</strong> <i>was</i><br/>"
                "<strong>all</strong>fun*!</p>\n"
            ),
        )

        # no html, no markdown
        evt.reply(message, allow_html=False, render_markdown=False)
        mock_az.intent.send_notice.assert_called_with(
            MatrixRoomID("#mock_room:example.org"),
            "**This** <i>was</i><br/><strong>all</strong>fun*!",
            html=None,
        )

        # no html, markdown
        evt.reply(message, allow_html=False, render_markdown=True)
        mock_az.intent.send_notice.assert_called_with(
            MatrixRoomID("#mock_room:example.org"),
            "**This** <i>was</i><br/><strong>all</strong>fun*!",
            html="<p><strong>This</strong> &lt;i&gt;was&lt;/i&gt;&lt;br/&gt;"
                 "&lt;strong&gt;all&lt;/strong&gt;fun*!</p>\n"
        )

    def test_reply_with_cmdprefix(self, command_processor: CommandProcessor, mocker: MockFixture
                                  ) -> None:
        mocker.patch("mautrix_telegram.user.config", self.config)

        evt = CommandEvent(
            processor=command_processor,
            room=MatrixRoomID("#mock_room:example.org"),
            event=MatrixEventID("$H45H:example.org"),
            sender=u.User(MatrixUserID("@sender:example.org")),
            command="help",
            args=[],
            is_management=False,
            is_portal=False,
        )

        mock_az = command_processor.az

        evt.reply("$cmdprefix+sp ....$cmdprefix+sp...$cmdprefix $cmdprefix", allow_html=False,
                  render_markdown=False)

        mock_az.intent.send_notice.assert_called_with(
            MatrixRoomID("#mock_room:example.org"),
            "tg ....tg+sp...tg tg",
            html=None,
        )

    def test_reply_with_cmdprefix_in_management_room(self, command_processor: CommandProcessor,
                                                     mocker: MockFixture) -> None:
        mocker.patch("mautrix_telegram.user.config", self.config)

        evt = CommandEvent(
            processor=command_processor,
            room=MatrixRoomID("#mock_room:example.org"),
            event=MatrixEventID("$H45H:example.org"),
            sender=u.User(MatrixUserID("@sender:example.org")),
            command="help",
            args=[],
            is_management=True,
            is_portal=False,
        )

        mock_az = command_processor.az

        evt.reply(
            "$cmdprefix+sp ....$cmdprefix+sp...$cmdprefix $cmdprefix",
            allow_html=True,
            render_markdown=True,
        )

        mock_az.intent.send_notice.assert_called_with(
            MatrixRoomID("#mock_room:example.org"),
            "....tg+sp...tg tg",
            html="<p>....tg+sp...tg tg</p>\n",
        )


class TestCommandHandler:
    config = Config("", "", "")
    config["bridge.permissions"] = {"*": "noperm"}

    @pytest.mark.parametrize(
        (
            "needs_auth,"
            "needs_puppeting,"
            "needs_matrix_puppeting,"
            "needs_admin,"
            "management_only,"
        ),
        [l for l in list_true_once_each(length=5)]
    )
    @pytest.mark.asyncio
    async def test_permissions_denied(
        self,
        needs_auth: bool,
        needs_puppeting: bool,
        needs_matrix_puppeting: bool,
        needs_admin: bool,
        management_only: bool,
        command_processor: CommandProcessor,
        boolean: bool,
        mocker: MockFixture,
    ) -> None:
        mocker.patch("mautrix_telegram.user.config", self.config)

        command = "testcmd"

        mock_handler = Mock()

        command_handler = CommandHandler(
            handler=mock_handler,
            needs_auth=needs_auth,
            needs_puppeting=needs_puppeting,
            needs_matrix_puppeting=needs_matrix_puppeting,
            needs_admin=needs_admin,
            management_only=management_only,
            name=command,
            help_text="No real command",
            help_args="mock mockmock",
            help_section=HelpSection("Mock Section", 42, ""),
        )

        sender = u.User(MatrixUserID("@sender:example.org"))
        sender.puppet_whitelisted = False
        sender.matrix_puppet_whitelisted = False
        sender.is_admin = False

        event = CommandEvent(
            processor=command_processor,
            room=MatrixRoomID("#mock_room:example.org"),
            event=MatrixEventID("$H45H:example.org"),
            sender=sender,
            command=command,
            args=[],
            is_management=False,
            is_portal=boolean,
        )

        assert await command_handler.get_permission_error(event)
        assert not command_handler.has_permission(False, False, False, False, False)

    @pytest.mark.parametrize(
        (
            "is_management,"
            "puppet_whitelisted,"
            "matrix_puppet_whitelisted,"
            "is_admin,"
            "is_logged_in,"
        ),
        [l for l in list_true_once_each(length=5)]
    )
    @pytest.mark.asyncio
    async def test_permission_granted(
        self,
        is_management: bool,
        puppet_whitelisted: bool,
        matrix_puppet_whitelisted: bool,
        is_admin: bool,
        is_logged_in: bool,
        command_processor: CommandProcessor,
        boolean: bool,
        mocker: MockFixture,
    ) -> None:
        mocker.patch("mautrix_telegram.user.config", self.config)

        command = "testcmd"

        mock_handler = Mock()

        command_handler = CommandHandler(
            handler=mock_handler,
            needs_auth=False,
            needs_puppeting=False,
            needs_matrix_puppeting=False,
            needs_admin=False,
            management_only=False,
            name=command,
            help_text="No real command",
            help_args="mock mockmock",
            help_section=HelpSection("Mock Section", 42, ""),
        )

        sender = u.User(MatrixUserID("@sender:example.org"))
        sender.puppet_whitelisted = puppet_whitelisted
        sender.matrix_puppet_whitelisted = matrix_puppet_whitelisted
        sender.is_admin = is_admin
        mocker.patch.object(u.User, 'is_logged_in', return_value=is_logged_in)

        event = CommandEvent(
            processor=command_processor,
            room=MatrixRoomID("#mock_room:example.org"),
            event=MatrixEventID("$H45H:example.org"),
            sender=sender,
            command=command,
            args=[],
            is_management=is_management,
            is_portal=boolean,
        )

        assert not await command_handler.get_permission_error(event)
        assert command_handler.has_permission(
            is_management=is_management,
            puppet_whitelisted=puppet_whitelisted,
            matrix_puppet_whitelisted=matrix_puppet_whitelisted,
            is_admin=is_admin,
            is_logged_in=is_logged_in,
        )


class TestCommandProcessor:
    config = Config("", "", "")
    config["bridge.command_prefix"] = "tg"
    config["bridge.permissions"] = {"*": "relaybot"}

    @pytest.mark.asyncio
    async def test_handle(self, command_processor: CommandProcessor, boolean2: Tuple[bool, bool],
                          mocker: MockFixture) -> None:
        mocker.patch('mautrix_telegram.user.config', self.config)
        mocker.patch(
            'mautrix_telegram.commands.handler.command_handlers',
            {"help": AsyncMock(), "unknown-command": AsyncMock()}
        )

        sender = u.User(MatrixUserID("@sender:example.org"))

        result = await command_processor.handle(
            room=MatrixRoomID("#mock_room:example.org"),
            event_id=MatrixEventID("$H45H:example.org"),
            sender=sender,
            command="hElp",
            args=[],
            is_management=boolean2[0],
            is_portal=boolean2[1],
        )

        assert result is None
        command_handlers = mautrix_telegram.commands.handler.command_handlers
        command_handlers["help"].mock.assert_called_once()  # type: ignore

    @pytest.mark.asyncio
    async def test_handle_unknown_command(self, command_processor: CommandProcessor,
                                          boolean2: Tuple[bool, bool], mocker: MockFixture) -> None:
        mocker.patch('mautrix_telegram.user.config', self.config)
        mocker.patch(
            'mautrix_telegram.commands.handler.command_handlers',
            {"help": AsyncMock(), "unknown-command": AsyncMock()}
        )

        sender = u.User(MatrixUserID("@sender:example.org"))
        sender.command_status = {}

        result = await command_processor.handle(
            room=MatrixRoomID("#mock_room:example.org"),
            event_id=MatrixEventID("$H45H:example.org"),
            sender=sender,
            command="foo",
            args=[],
            is_management=boolean2[0],
            is_portal=boolean2[1],
        )

        assert result is None
        command_handlers = mautrix_telegram.commands.handler.command_handlers
        command_handlers["help"].mock.assert_not_called()  # type: ignore
        command_handlers["unknown-command"].mock.assert_called_once()  # type: ignore

    @pytest.mark.asyncio
    async def test_handle_delegated_handler(self, command_processor: CommandProcessor,
                                            boolean2: Tuple[bool, bool],
                                            mocker: MockFixture) -> None:
        mocker.patch('mautrix_telegram.user.config', self.config)
        mocker.patch(
            'mautrix_telegram.commands.handler.command_handlers',
            {"help": AsyncMock(), "unknown-command": AsyncMock()}
        )

        sender = u.User(MatrixUserID("@sender:example.org"))
        sender.command_status = {"foo": AsyncMock(), "next": AsyncMock()}

        result = await command_processor.handle(
            room=MatrixRoomID("#mock_room:example.org"),
            event_id=MatrixEventID("$H45H:example.org"),
            sender=sender,  # u.User
            command="foo",
            args=[],
            is_management=boolean2[0],
            is_portal=boolean2[1]
        )

        assert result is None
        command_handlers = mautrix_telegram.commands.handler.command_handlers
        command_handlers["help"].mock.assert_not_called()  # type: ignore
        command_handlers["unknown-command"].mock.assert_not_called()  # type: ignore
        sender.command_status["foo"].mock.assert_not_called()  # type: ignore
        sender.command_status["next"].mock.assert_called_once()  # type: ignore
