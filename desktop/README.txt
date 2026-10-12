Alberich for your computer
==========================

This is the full Alberich: your bank and card statements, receipts, the review queue, the treasury and the rest. It also keeps a copy of your phone's pouches.
You do not need it for the basics: the phone app has the pouches, the pet, reminders and the widget on its own. Everything stays on your computer and your phone.

To start:    python3 start.py      (on Windows:  py start.py)
It sets itself up the first time. Your browser opens; choose a passphrase when it asks.

To connect your phone:
  1. Install the Alberich app on your phone (the Phone page here has the file).
  2. On the Phone page, make a pairing code, scan it with your phone's camera, and tap the link.
  3. Your phone and this computer must be on the same Wi-Fi (or both on Tailscale).
  If your firewall asks about Python, allow it on private networks: that is the phone's door.

Optional extras (the app tells you when one is missing):
  - Reading photographed receipts needs the Tesseract program installed on your computer.
  - The assistant needs an Anthropic key in the environment variable ANTHROPIC_API_KEY. Without one, it is simply switched off.

Where things are:   data/   (your database, certificate and files). Back up that folder.
To stop:            press Ctrl+C in this window.
