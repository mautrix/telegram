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
	<title>Matrix login - Mautrix-Telegram bridge</title>
	<link rel="icon" type="image/png" href="favicon.png"/>
	<meta property="og:title" content="Matrix login - Mautrix-Telegram bridge">
	<meta property="og:description" content="A hybrid puppeting/relaybot Matrix-Telegram bridge">
	<meta property="og:image" content="favicon.png">
	<meta charset="utf-8">
	<link rel="stylesheet" href="//fonts.googleapis.com/css?family=Roboto:300,700">
	<link rel="stylesheet"
		  href="https://cdnjs.cloudflare.com/ajax/libs/normalize/8.0.1/normalize.min.css">
	<link rel="stylesheet"
		  href="https://cdnjs.cloudflare.com/ajax/libs/milligram/1.3.0/milligram.min.css">
	<link rel="stylesheet" href="login.css"/>
</head>
<body>
<main class="container">
	% if state == "logged-in":
		<h1>Logged in successfully!</h1>
		<p>
			Logged in as ${mxid}.
			You can now close this page.
		</p>
	% elif state == "already-logged-in":
		<h1>You're already logged in!</h1>
		<p>
			If you want to log in with another account, log out using the
			<code>logout-matrix</code> management command first.
		</p>
	% elif state == "invalid-token":
		<h1>Invalid or expired token</h1>
		<div class="error">Please ask the bridge bot for a new login link.</div>
	% else:
		<h1>Log in to Matrix</h1>
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

				<input id="access_token" type="radio" name="mode" value="access_token" checked>
				<label for="access_token">Access token</label><br>
				<input id="password" type="radio" name="mode" value="password" disabled>
				<label for="password">Password</label><br>

				<label for="value">Value</label>
				<input type="text" id="value" name="value"
					   placeholder="Enter Matrix access token or password"/>

				<button type="submit">Sign in</button>
			</fieldset>
		</form>
	% endif
</main>
</body>
</html>
