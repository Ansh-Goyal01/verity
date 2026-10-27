# Verity trip logger

This page records real EV trips for the Verity team. It saves vehicle and trip details, location points, and battery readings on your phone. It does not calculate whether a trip is safe or whether the vehicle will reach its destination.

## Before starting

1. Open the team's **GitHub Pages HTTPS link** in Chrome on your phone. Ask the team for the link if you do not have it. Phones generally require HTTPS for GPS, so opening the file directly may not allow location access.
2. Allow location access when your phone asks. Turn on your phone's location setting. The logger only saves points with accuracy of 50 metres or better.
3. Keep the page open and visible where possible. Browsers and phones may pause GPS, reminders, or the screen-awake feature when the page is in the background or the phone is locked. Battery and power-saving settings can also affect recording.
4. Only enter battery % when safely stopped, or let a passenger do it.

## Start and record a trip

1. Fill in the vehicle make, model, variant, battery size and type. The last vehicle is remembered on this phone.
2. Enter your anonymous driver ID, passengers, cargo and AC setting. Tap **Continue to start battery**.
3. Enter the starting battery percentage using the keypad, then tap **Save battery %**. Allow location access if asked. Check that the GPS point count increases.
4. During the trip, enter battery percentages whenever safely stopped. The page reminds you every 10 minutes while it can run. Voice entry appears only on supported browsers; check the recognised number before saving it.
5. The trip saves on this phone as it is recorded. If the page reloads, reopen the same link in the same browser and allow GPS again if asked. Location cannot be recorded while the page is closed.

## End, download and send

1. At the destination, safely stop and enter the final battery percentage. Add optional notes.
2. Tap **End trip**. At least one accurate GPS point and a final battery entry are required. If the page asks you to fix something, follow its message and try again.
3. Under **Saved trips**, tap **Download**. If your browser supports sharing JSON files, you can also tap **Share** and choose the team's agreed channel.
4. Keep the suggested filename: `trip_<driver_id>_<date>_<time>.json`. The date and time are taken from the trip start on your phone. Send that JSON file to the team through the channel your team chooses; the logger does not send it automatically. Do not change the file contents.
5. After the team confirms receipt, you can delete the saved trip using **Delete**. It asks for confirmation.

Saved trips are stored in the browser on this phone, not in a team account. Clearing browser data, private browsing, low storage, or changing browsers may remove or prevent access to them. Download and send files promptly. The distance shown is an estimate from saved GPS points; battery change is the difference between the first and last readings and may include charging.
