# mechanism/alerts/texts.py
"""
Wording shared by the channel digest and the interactive bot. The audience is public, so the framing is fixed here in one
place: EDUCATIONAL information from public price data, never advice. test_digest.py scans every string in this module and
in the rendered messages for advice-style words (buy / sell / target / stop / entry / recommend / signal / ...).
"""

# The channel's per-post disclaimer: ONE line at the foot of every channel post and image. The full, plain-language disclaimer lives in the pinned
# "Start here" post (channel_posts.START_HERE) and in the twice-daily notice (channel_content.post_disclaimer). The private assistant keeps the
# longer wording below (it is shown once, behind an acknowledgement button).
DISCLAIMER_ONE_LINE = "* Not investment advice."

DISCLAIMER_SHORT = ("Educational information from public price data, ranked by the stated formulas. Not investment advice "
                    "and not a suggestion to trade any security. Everyone makes their own decisions.")

DISCLAIMER_LONG = (
    "This bot shows <b>educational market data</b>. It reads the same end-of-day scan as the channel and lets you "
    "follow your own list of stocks.\n\n"
    "It is not investment advice, not an offer, and not a suggestion to trade any security. It knows nothing about your "
    "situation. Data comes from public price sources, is end-of-day, and can be delayed or wrong. Everyone makes their own "
    "decisions about their own investing and strategies, and carries the results themselves.")

ACK_BUTTON = "I understand - continue"
ACK_HINT = "Tap the button below to continue. If the button does not respond, send /agree instead."

# Access (PRIVATE_ASSISTANT_PLAN.md section 3). The bot is invite-only; a person who is not let in gets exactly one of these and
# nothing is stored about them. {uid} is their OWN Telegram id, which they can pass to the owner.
NOT_INVITED = ("This is the First Light private assistant. Access is on request, and seats are limited.\n\n"
               "Tap <b>Request access</b> and the owner will review it. If you already have an invitation link, open it again.\n\n"
               "Your Telegram id: <code>{uid}</code>")
NOT_INVITED_CLOSED = ("This is the First Light private assistant. Access is closed for now.\n\n"
                      "If you already have an invitation link, open it again.\n\nYour Telegram id: <code>{uid}</code>")
REQUEST_PENDING = "Your request is waiting for the owner. You will hear back here."
REQUEST_COOLDOWN = "Your last request was not approved. You can ask again after {date}."
REQUEST_BUTTON, WHAT_BUTTON, BACK_TO_ACCESS = "Request access", "What is this?", "Back"
WHAT_IS_THIS = (
    "<b>The First Light private assistant</b>\n"
    "It reads the same end-of-day scan as the First Light channel and lets you follow your own watchlist and portfolio since the day "
    "you add a stock, with news and a chart for each stock.\n\n"
    "Access is on request, and the owner approves each one. If you request access, your Telegram id and the time are stored until the "
    "owner decides (at most 14 days). Nothing is stored if you do not tap the button.\n\n"
    "Educational data from public price sources, not investment advice.")
REQUEST_SENT = "Thanks - your request was sent to the owner. You will hear back here."
REQUEST_POPUP_SENT = "Request sent"
REQUEST_POPUP_ALREADY = "Your request is already waiting."
REQUEST_POPUP_CLOSED = "Access is closed for now."
REQUEST_POPUP_FULL = "The waiting list is full. Please try again later."
REQUEST_POPUP_BLOCKED = "This account cannot request access."
REQUEST_POPUP_MEMBER = "You already have access. Send /start."
REQUEST_POPUP_COOLDOWN = "Your last request was not approved. You can ask again after {date}."
ACCESS_GRANTED_USER = "You are in. Welcome to the First Light assistant."
ACCESS_DECLINED_USER = "Your request was not approved this time. You can ask again after {date}."
OWNER_REQUEST_NOTICE = "Access request from Telegram id <code>{uid}</code>."
OWNER_AUTO_NOTICE = "Access granted automatically (open beta) to Telegram id <code>{uid}</code>."
OWNER_APPROVED, OWNER_DECLINED = "Approved <code>{uid}</code>.", "Declined <code>{uid}</code>."
OWNER_NO_REQUEST = "No waiting request for {uid} (already handled or expired)."
OWNER_NOT_NOTIFIED = " (they could not be notified - they may have blocked the bot)"
NOT_INVITED_POPUP = "Private assistant - invitation required."
INVITE_INVALID = ("This invitation link is not valid any more: it was already used or it expired. "
                  "Ask the owner for a new one.\n\nYour Telegram id: <code>{uid}</code>")
INVITE_REVOKED = "This account no longer has access to the assistant."
WELCOME_INVITED = "Welcome. Your invitation was accepted."

# Group chats are public to everyone in the group, so the bot only points to the private chat there and never shows data.
GROUP_POINTER = "This is a private assistant and it does not answer in groups. Open a private chat with the bot."
OPEN_PRIVATE_CHAT = "Open the private chat"
ERROR_REPLY = "Something went wrong on our side. Please try again in a minute."

# Popup texts (Telegram limits an alert to 200 characters).
DEFINITIONS = {
    "atr": "ATR (average true range) = a stock's average daily price range over 14 days. \"range 3.6× ATR\" means today's "
           "range was 3.6 times that average.",
    "vol": "\"vol 31.0×\" = today's trading volume divided by the median daily volume of the previous 50 sessions.",
    "groups": "Breakout: closed above the highest high of the prior 20 sessions. Near breakout: within 3% below that high, "
              "not through it. Lists rank stocks with at least $5M average daily trading value.",
}

# Always visible under the levels (never inside a collapsed block): the one caveat a reader must not miss.
LEVELS_NOTE = ("These are distances, not predictions. Nothing says price will reach any level, and prices can gap through "
               "any of them.")

# Collapsed by default: how the numbers are built, and what our own history test found.
LEVELS_HOW = (
    "How this is built: the risk level is the last close minus 2 x ATR(14). The reference levels are the close plus 2, 4 and "
    "6 x ATR, which are 1R, 2R and 3R (R = the distance from the close to the risk level). The same arithmetic is applied to "
    "every stock. The reward-to-risk numbers compare distances, not chances.\n\n"
    "In our own 2018-2025 test on daily top gainers, fixed ATR levels like these were roughly break-even on average (a "
    "survivorship-biased universe and a sample heavy with bull markets).")

HELP = (
    "<b>Menu</b> (the buttons at the bottom)\n"
    "Today's lists - Portfolio - Watchlist - Help\n\n"
    "<b>Commands</b>\n"
    "/today - the channel's lists, each stock shown once\n"
    "/stock AAPL - a stock card (or just type the ticker)\n"
    "/add AAPL - add to your watchlist at the last close\n"
    "/add AAPL 140.5 - add to your watchlist at your own price\n"
    "/add AAPL 140.5 10 - add to your portfolio: price and shares\n"
    "/remove AAPL - remove a stock from your lists\n"
    "/portfolio and /watchlist - how each stock changed since you added it\n"
    "/levels AAPL - the ATR risk framework (educational)\n"
    "/full - every stock in a group, sorted three ways, and /aligned - breakouts near their 20-week or 52-week high\n"
    "/screen breakout vol&gt;3 - your own filter over today's scan (send /screen for the rules)\n"
    "/history AAPL - a stock's past 20-day-high breakouts and what followed\n"
    "/week - how your own lists changed over the last 5 sessions\n"
    "/scan - the whole daily scan as a file\n"
    "/morning on - a short private message after each scan when one of your own stocks changed (off by default)\n"
    "/export - your saved data as a file, /deleteme - erase it\n"
    "/guide - the quick tour, /privacy - what is stored, /about - what this is\n\n"
    "Any stock in the daily scan (liquid US stocks) can be added.")

OWNER_HELP = (
    "\n\n<b>Owner</b>\n"
    "/invite Dana - a one-time invitation link (valid {hours} hours; the note is only for you)\n"
    "/approve 123456789 - give a Telegram id access\n"
    "/revoke 123456789 - block an id (its saved data is deleted after {days} days)\n"
    "/users - how many people have access\n"
    "/requests - who is waiting for access (with Approve / Decline)\n"
    "/funnel - counts: channel opens, requests, approvals, first stocks tracked\n"
    "/status - snapshot age and access counts")

ABOUT = DISCLAIMER_LONG


# ---------------------------------------------------------------------------------------------------------------------------
# Private assistant UI copy (BOT_DESIGN_REPORT.md). Wording rules: facts and the user's own numbers; "your price", never "entry";
# nothing that tells anyone what to do (the wording-guard test scans every string here and every rendered screen).
# ---------------------------------------------------------------------------------------------------------------------------
MENU_TODAY, MENU_PORTFOLIO, MENU_WATCHLIST, MENU_HELP = "Today's lists", "Portfolio", "Watchlist", "Help"

ONBOARDING = {
    1: ("<b>Welcome to First Light</b>\n\n"
        "This is your private assistant. It reads the same end-of-day scan as the channel and helps you follow the stocks you "
        "care about.\n\nOther users cannot see your lists.\n\n<i>Step 1 of 4</i>"),
    2: ("<b>How it works</b>\n\n"
        "<b>1. Today's lists</b> - every stock from the channel's lists, each shown once.\n"
        "<b>2. Tap a stock</b> - its numbers, news and a chart.\n"
        "<b>3. Add it</b> to your Watchlist or Portfolio - the bot remembers your price and the day.\n"
        "<b>4. Come back any time</b> - see how each stock changed since that day.\n\n<i>Step 2 of 4</i>"),
    3: ("<b>Your data</b>\n\n"
        "Stored: the symbols you add, the price you give (or the last close), the day, and optionally your shares. Nothing else.\n\n"
        "Prices are end-of-day. /export gives you everything you saved and /deleteme erases it.\n\n"
        "The owner of this bot can technically read the database.\n\n<i>Step 3 of 4</i>"),
}
ONBOARDING_LAST = DISCLAIMER_LONG + "\n\n<i>Step 4 of 4</i>"
ONBOARDING_DONE = (
    "<b>You are all set.</b>\n\n"
    "Quick start:\n1. Open Today's lists\n2. Tap a stock and add it to a list\n3. Come back tomorrow and see the change\n\n"
    "The menu at the bottom is always there. You can also type any ticker, for example AAPL, to open its card.")
NEXT, BACK_BTN, SKIP_GUIDE = "Next", "Back", "Skip guide"

PRIVACY = (
    "<b>What is stored</b>\n"
    "Your Telegram id, the day you accepted the notice, and for each stock you add: the symbol, your price (or the last close), the "
    "day, and your shares if you gave them. No names, no messages. If you asked for access, your Telegram id and the time of the "
    "request were kept only until the owner decided (at most 14 days).\n\n"
    "If you turn on /morning, one switch and the last session messaged are stored, nothing else; /morning off or /deleteme removes it.\n\n"
    "Only you see your lists. The owner of this bot can technically read the database.\n\n"
    "/export sends everything you saved as a file. /deleteme erases it after you confirm.")
DELETE_CONFIRM = "Erase everything you saved (your watchlist and your portfolio)? This cannot be undone."
DELETED = "Done. Your saved lists were erased."
NOTHING_TO_ERASE = "You had nothing saved."
EXPORT_EMPTY = "You have nothing saved yet, so there is nothing to export."

MORNING_USAGE = "Use <code>/morning on</code> or <code>/morning off</code>."
MORNING_ON = ("Morning message is on. After each daily scan you get a short message, only when one of your own stocks changed "
              "(entered or left a group, is in the channel's lists, or moved at least 2× its ATR). The first one comes with the next scan. "
              "Turn it off any time with /morning off.")
MORNING_OFF = "Morning message is off. Nothing about it is stored."
MORNING_STATUS_ON = "The morning message is on. Turn it off with /morning off."
MORNING_STATUS_OFF = "The morning message is off. Turn it on with /morning on."
