<!DOCTYPE html>
<html>
<head>
	<title>Mautrix-Telegram bridge</title>
	<link rel="icon" type="image/png" href="favicon.png"/>
	<meta property="og:title" content="Mautrix-Telegram bridge">
	<meta property="og:description" content="A hybrid puppeting/relaybot Matrix-Telegram bridge">
	<meta property="og:image" content="favicon.png">
	<meta charset="utf-8">
	<link rel="stylesheet" href="login.css"/>
</head>
<body>
<main>
	% if state == "logged-in":
		<h1>Logged in successfully!</h1>
		<p>Logged in as @${username}</p>
	% else:
		<h1>Log in to Telegram</h1>
		% if error:
			<div class="error">${error}</div>
		% endif
		% if message:
			<div class="message">${message}</div>
		% endif
		<form method="post">
			<input type="text" name="mxid" placeholder="Enter Matrix ID" value="${mxid}"/>
			% if state == "request":
				<input type="text" name="phone" placeholder="Enter phone number"/>
				<button type="submit">Request code</button>
			% elif state == "code":
				<input type="number" name="code" placeholder="Enter phone code"/>
				<button type="submit">Sign in</button>
			% elif state == "password":
				<input type="password" name="password" placeholder="Enter password"/>
				<button type="submit">Sign in</button>
			% endif
		</form>
	% endif
</main>
</body>
</html>
