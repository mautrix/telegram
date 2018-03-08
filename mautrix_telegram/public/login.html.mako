<!--
mautrix-telegram - A Matrix-Telegram puppeting bridge
Copyright (C) 2018 Tulir Asokan

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
<html>
<head>
	<title>Mautrix-Telegram bridge</title>
	<link rel="icon" type="image/png" href="favicon.png"/>
	<meta property="og:title" content="Mautrix-Telegram bridge">
	<meta property="og:description" content="A hybrid puppeting/relaybot Matrix-Telegram bridge">
	<meta property="og:image" content="favicon.png">
	<meta charset="utf-8">
	<link rel="stylesheet" href="//fonts.googleapis.com/css?family=Roboto:300,700">
	<link rel="stylesheet" href="//cdn.rawgit.com/necolas/normalize.css/master/normalize.css">
	<link rel="stylesheet"
		  href="//cdn.rawgit.com/milligram/milligram/master/dist/milligram.min.css">
	<link rel="stylesheet" href="login.css"/>
</head>
<body>
<main class="container">
	% if username:
		% if state == "logged-in":
			<h1>Logged in successfully!</h1>
			<p>
				Logged in as @${username}.
				You can now close this page.
				You should be invited to Telegram portals on Matrix momentarily.
			</p>
		% else:
			<h1>You're already logged in!</h1>
			<p>
				You're logged in as @${username}.
			</p>
			<p>
				If you want to log in with another account, log out using the <code>logout</code>
				management command first.
			</p>
		% endif
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
				<input type="text" id="mxid" name="mxid" placeholder="Enter Matrix ID"
					   value="${mxid}"/>
				% if state == "request":
					<label for="value">Phone number</label>
					<input type="tel" id="value" name="phone" placeholder="Enter phone number"/>
					<button type="submit">Request code</button>
				% elif state == "code":
					<label for="value">Phone code</label>
					<input type="number" id="value" name="code" placeholder="Enter phone code"/>
					<button type="submit">Sign in</button>
					<div class="float-right">
						<button class="button-clear" type="button"
								onclick="location.replace(location.href)">
							Go back
						</button>
					</div>
				% elif state == "password":
					<label for="value">Password</label>
					<input type="password" id="value" name="password"
						   placeholder="Enter password"/>
					<button type="submit">Sign in</button>
					<div class="float-right">
						<button class="button-clear" type="button"
								onclick="location.replace(location.href)">
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
