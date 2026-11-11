#!/usr/bin/env python3
"""notify.py - gmail + windows toast alerts.

Env:
    QB_GMAIL_USER           gmail address alerts are sent from
    QB_GMAIL_APP_PASSWORD   gmail app password (not the account password)
    QB_GMAIL_TO             recipient, defaults to QB_GMAIL_USER
    QB_TOAST                1/true to also raise a desktop toast
    QB_NOTIFY_COOLDOWN      seconds before the same key repeats (default 60)

Nothing here is allowed to break the engine. With no credentials configured
`from_env()` returns a notifier whose `enabled` is False and whose `send` only
writes to the log. Delivery runs on a daemon thread so a slow SMTP handshake
can never stall a tick.
"""

from __future__ import annotations

import logging
import os
import smtplib
import subprocess
import threading
import time
from email.message import EmailMessage

LOG = logging.getLogger("notify")

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
DEFAULT_COOLDOWN = 60.0

# Windows needs an AppUserModelID to attribute a toast to; without one
# CreateToastNotifier() throws "Element not found" (0x80070490). PowerShell's
# own id is registered on every Windows install.
TOAST_APP_ID = (
    r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
)

TOAST_SCRIPT = """
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(
    [Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$n = $t.GetElementsByTagName('text')
$n.Item(0).AppendChild($t.CreateTextNode($env:QB_TOAST_TITLE)) > $null
$n.Item(1).AppendChild($t.CreateTextNode($env:QB_TOAST_BODY)) > $null
if ($env:QB_TOAST_APP) {
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier(
        $env:QB_TOAST_APP).Show(
        (New-Object Windows.UI.Notifications.ToastNotification $t))
} else {
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier().Show(
        (New-Object Windows.UI.Notifications.ToastNotification $t))
}
"""


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Notifier:
    def __init__(
        self,
        user: str = "",
        app_password: str = "",
        to: str = "",
        toast: bool = False,
        cooldown: float = DEFAULT_COOLDOWN,
    ) -> None:
        self.user = user.strip()
        self.app_password = app_password.replace(" ", "").strip()
        self.to = (to or self.user).strip()
        self.toast = toast
        self.cooldown = max(0.0, float(cooldown))
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        """True when email can actually be sent."""
        return bool(self.user and self.app_password and self.to)

    def __bool__(self) -> bool:
        # The engine checks `if self.notifier:` before building messages, so an
        # unconfigured notifier must be falsy to keep that work off the hot path.
        return self.enabled or self.toast

    def send(self, title: str, body: str, key: str = None) -> None:  # type: ignore[assignment]
        """Queue an alert. Never raises, never blocks."""
        now = time.time()
        stamp = key or title
        with self._lock:
            last = self._last.get(stamp)
            if last is not None and now - last < self.cooldown:
                LOG.debug("alert suppressed (cooldown): %s", stamp)
                return
            self._last[stamp] = now

        LOG.info("ALERT %s - %s", title, body.replace("\n", " | "))
        if not self.enabled and not self.toast:
            return
        threading.Thread(
            target=self._deliver, args=(title, body), daemon=True, name="notify"
        ).start()

    def _deliver(self, title: str, body: str) -> None:
        if self.toast:
            try:
                self._toast(title, body)
            except Exception as exc:  # noqa: BLE001 - alerting must never raise
                LOG.debug("toast failed: %s", exc)
        if self.enabled:
            try:
                self._email(title, body)
            except Exception as exc:  # noqa: BLE001
                LOG.warning("email alert failed: %s", exc)

    def _email(self, title: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"] = self.user
        msg["To"] = self.to
        msg["Subject"] = title.strip()[:180] or "quantbot alert"
        msg.set_content(body)
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15) as smtp:
            smtp.login(self.user, self.app_password)
            smtp.send_message(msg)
        LOG.info("email sent to %s", self.to)

    def _toast(self, title: str, body: str) -> None:
        if os.name != "nt":
            return
        env = dict(os.environ)
        env["QB_TOAST_TITLE"] = title.strip()[:120]
        env["QB_TOAST_BODY"] = (body.splitlines() or [""])[0][:200]
        env["QB_TOAST_APP"] = os.getenv("QB_TOAST_APP") or TOAST_APP_ID
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", TOAST_SCRIPT],
            env=env,
            timeout=20,
            check=False,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def from_env() -> Notifier:
    """Build a notifier from the environment. Always returns an object."""
    try:
        cooldown = float(os.getenv("QB_NOTIFY_COOLDOWN", DEFAULT_COOLDOWN))
    except ValueError:
        cooldown = DEFAULT_COOLDOWN
    return Notifier(
        user=os.getenv("QB_GMAIL_USER", ""),
        app_password=os.getenv("QB_GMAIL_APP_PASSWORD", ""),
        to=os.getenv("QB_GMAIL_TO", ""),
        toast=_truthy(os.getenv("QB_TOAST", "")),
        cooldown=cooldown,
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    import envfile

    envfile.load()
    n = from_env()
    print(f"email ready: {n.enabled}   toast ready: {n.toast}")
    if not n.enabled:
        print("set QB_GMAIL_USER / QB_GMAIL_APP_PASSWORD / QB_GMAIL_TO to enable email")
    n.send("quantbot test", "If you can read this, alerts work.", key="test")
    time.sleep(3)  # let the daemon thread finish before the process exits
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
