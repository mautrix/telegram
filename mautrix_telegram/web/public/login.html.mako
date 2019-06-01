<!--
mautrix-telegram - A Matrix-Telegram puppeting bridge
Copyright (C) 2019 Tulir Asokan

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
-->
<!DOCTYPE html>
<html lang="en">
<head>
	<title>Login - Mautrix-Telegram bridge</title>
	<link rel="icon" type="image/png" href="favicon.png"/>
	<meta property="og:title" content="Login - Mautrix-Telegram bridge">
	<meta property="og:description" content="A hybrid puppeting/relaybot Matrix-Telegram bridge">
	<meta property="og:image" content="favicon.png">
	<meta charset="utf-8">
	<link rel="stylesheet" href="//fonts.googleapis.com/css?family=Roboto:300,700">
	<link rel="stylesheet"
		  href="https://cdnjs.cloudflare.com/ajax/libs/normalize/8.0.1/normalize.min.css">
	<link rel="stylesheet"
		  href="https://cdnjs.cloudflare.com/ajax/libs/milligram/1.3.0/milligram.min.css">
	<link rel="stylesheet" href="login.css"/>

	<script>
		function switchToBotLogin() {
			const params = new URLSearchParams(location.search.slice(1))
			params.set("mode", "bot")
			location.search = "?" + params.toString()
			console.log(location.search)
		}

		function goBack() {
			let params = new URLSearchParams(location.search.slice(1))
			const token = params.get("token")
			params = new URLSearchParams()
			if (token) {
				params.set("token", token)
			}
			location.replace(location.href.split("?")[0] + "?" + params.toString())
		}
	</script>
</head>
<body>
<main class="container">
	% if human_tg_id:
		% if state == "logged-in":
			<h1>Logged in successfully!</h1>
			<p>
				Logged in as ${human_tg_id}.
				You can now close this page.
				You should be invited to Telegram portals on Matrix momentarily.
			</p>
		% elif state == "bot-logged-in":
			<h1>Logged in successfully!</h1>
			<p>
				Logged in as ${human_tg_id}.
				You can now close this page.
				You should be invited to Telegram portals on Matrix momentarily.
			</p>
		% else:
			<h1>You're already logged in!</h1>
			<p>
				You're logged in as ${human_tg_id}.
			</p>
			<p>
				If you want to log in with another account, log out using the <code>logout</code>
				management command first.
			</p>
		% endif
	% elif state == "invalid-token":
		<h1>Invalid or expired token</h1>
		<div class="error">Please ask the bridge bot for a new login link.</div>
	% else:
		<h1>Log in to Telegram</h1>
	% if error:
		<div class="error">${error}</div>
	% endif
	% if message:
		<div class="message">${message}</div>
	% endif
		<form method="post">
			<fieldset>
				<label for="mxid">Matrix ID</label>
				<input type="text" id="mxid" name="mxid" disabled value="${mxid}"/>
				% if state == "request":
					<label for="value">Phone number</label>
					<input type="tel" id="value" name="phone" placeholder="Enter phone number"/>
					<button type="submit">Start</button>
					<button class="button-clear float-right" type="button" onclick="switchToBotLogin()">
						Use bot token
					</button>
				% elif state == "bot_token":
					<label for="value">Bot token</label>
					<input type="text" id="value" name="bot_token"
						   placeholder="Enter bot API token"/>
					<button type="submit">Sign in</button>
				% elif state == "code":
					<label for="value">Phone code</label>
					<input type="number" id="value" name="code" placeholder="Enter phone code"/>
					<button type="submit">Sign in</button>
				% elif state == "password":
					<label for="value">Password</label>
					<input type="password" id="value" name="password"
						   placeholder="Enter password"/>
					<button type="submit">Sign in</button>
				% endif
				% if state != "request":
					<div class="float-right">
						<button class="button-clear" type="button" onclick="goBack()">
							Go back
						</button>
					</div>
				% endif
			</fieldset>
		</form>
	% endif
</main>
</body>
</html>
